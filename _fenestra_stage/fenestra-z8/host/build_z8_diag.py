"""Build the minimal Z8 C3.11 diagnostic ELF; image output requires separate validation.

Addresses and original windows must come from a Z8-specific binding profile.
The firmware input is the 81,807,709-byte MAIN component without its two-byte
component CRC, not the 81,807,711-byte EG component or the encrypted updater.
No camera, firmware entry, USB device or updater is executed by this builder.
Upstream Fenestra LICENSE and EXCEPTIONS apply to this project-owned adaptation.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess

from compiler_dependencies import dependencies, identity_configuration, finalize_identity

ROOT = Path(__file__).resolve().parents[1]
MAIN_BYTES = 81807709
MAIN_SHA256 = '2b43a31c9de838f84b12416de4348a7004e65be07f853ec56ec2b10f3a8e3853'
LLVM_DEFAULT = Path('C:/Program Files/Microsoft Visual Studio/2022/Community/VC/Tools/Llvm/x64/bin')
NATIVE = ('allocate', 'task_create', 'task_resume', 'task_delay', 'ptp_payload',
          'ptp_capacity', 'ptp_incoming', 'ptp_response', 'ptp_send')
HOOKS = {
    'startup': ('fx_z8_system_dispatch', 'fx_z8_system_original'),
    'ptp_lookup': ('fx_z8_ptp_lookup', 'fx_z8_ptp_original_lookup'),
    'ptp_session': ('fx_z8_ptp_session', 'fx_z8_ptp_original_session'),
    'ptp_operations': ('fx_z8_ptp_operations', 'fx_z8_ptp_original_operations'),
}
VALIDATION_CHECKS = ('task_abi', 'ptp_abi', 'events', 'anchor', 'native_bindings',
                     'hook_windows', 'executable_space', 'scratch_x16')
SOURCES = ('src/platform/z8_c311/diag.c', 'src/platform/z8_c311/ptp.c',
           'src/platform/z8_c311/trampolines.S', 'src/ptp.c', 'src/native_ptp.c',
           'src/ptp_device_info.c', 'src/memory.c')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def integer(value, name, low=0, high=(1 << 64)-1):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError('missing or non-integer binding: '+name)
    try:
        result = int(value, 0) if isinstance(value, str) else value
    except ValueError as exc:
        raise ValueError('invalid integer binding: '+name) from exc
    if not low <= result <= high:
        raise ValueError('binding outside allowed range: '+name)
    return result


def octets(value, name, size):
    if not isinstance(value, str):
        raise ValueError('missing original byte evidence: '+name)
    try:
        data = bytes.fromhex(value)
    except ValueError as exc:
        raise ValueError('invalid original byte evidence: '+name) from exc
    if len(data) != size:
        raise ValueError('wrong original evidence length: '+name)
    return data


def object_field(parent, name):
    value = parent.get(name)
    if not isinstance(value, dict):
        raise ValueError('missing binding object: '+name)
    return value


def copyable(window):
    """A deliberately small rejection filter, not a general A64 relocator.

    Separate native validation must establish the entry ABI, straight-line
    prologue semantics and x16 scratch-register liveness at the continuation.
    """
    for word, in struct.iter_unpack('<I', window):
        if not word:
            raise ValueError('undefined zero instruction in displaced window')
        if (word & 0x1f000000 == 0x10000000 or  # ADR/ADRP
            word & 0x7c000000 == 0x14000000 or  # B/BL immediate
            word & 0xff000000 == 0x54000000 or  # B.cond
            word & 0x7e000000 == 0x34000000 or  # CBZ/CBNZ
            word & 0x7e000000 == 0x36000000 or  # TBZ/TBNZ
            word & 0x3b000000 == 0x18000000 or  # literal load/prefetch
            word & 0xfe000000 == 0xd6000000 or  # register branch/return
            word & 0xff000000 == 0xd4000000):   # exception instruction
            raise ValueError('unsupported displaced A64 instruction: '+hex(word))


def load_bindings(path):
    raw = Path(path).read_bytes()
    p = json.loads(raw)
    if p.get('schema') != 'fenestra.z8-diag-bindings/v1' or p.get('target') != 'z8-c311':
        raise ValueError('wrong Z8 diagnostic binding schema/target')
    app = object_field(p, 'application')
    app['base'] = integer(app.get('base'), 'application.base', 1)
    if integer(app.get('bytes'), 'application.bytes') != MAIN_BYTES or app.get('sha256') != MAIN_SHA256:
        raise ValueError('profile is not the pinned Z8 C3.11 MAIN component without CRC')
    code, ram = object_field(p, 'code'), object_field(p, 'ram')
    for name, item in (('code', code), ('ram', ram)):
        for key in ('begin', 'end'):
            item[key] = integer(item.get(key), name+'.'+key, 1)
        if item['begin'] >= item['end'] or item['begin'] % 4096 or item['end'] % 4096:
            raise ValueError(name+' bounds must be nonempty and page aligned')
    if not app['base']+0x40000 <= code['begin'] < code['end'] <= app['base']+MAIN_BYTES:
        raise ValueError('code reservation must be inside MAIN after its boot prefix')
    if not 4096 <= code['end']-code['begin'] <= 0x200000:
        raise ValueError('unexpected diagnostic code reservation size')
    native = object_field(p, 'native')
    for key in NATIVE:
        native[key] = integer(native.get(key), 'native.'+key, app['base'], app['base']+MAIN_BYTES-4)
        if native[key] % 4:
            raise ValueError('unaligned native binding: '+key)
    data = object_field(p, 'data')
    for key, size in (('page_roots', 56), ('ptp_session', 4), ('ptp_operations', 4)):
        data[key] = integer(data.get(key), 'data.'+key, ram['begin'], ram['end']-size)
        if data[key] % (8 if key == 'page_roots' else 4):
            raise ValueError('unaligned native data binding: '+key)
    data['ptp_operations_capacity'] = integer(data.get('ptp_operations_capacity'), 'data.ptp_operations_capacity', 4, 65536)
    if data['ptp_operations']+data['ptp_operations_capacity'] > ram['end']:
        raise ValueError('PTP operation array extends outside RAM')
    anchor = object_field(p, 'anchor')
    anchor['group'] = integer(anchor.get('group'), 'anchor.group', 0, 6)
    anchor['id'] = integer(anchor.get('id'), 'anchor.id', 1, 65535)
    anchor['descriptor'] = integer(anchor.get('descriptor'), 'anchor.descriptor', ram['begin'], ram['end']-64)
    if anchor['descriptor'] % 8:
        raise ValueError('unaligned anchor descriptor')
    if integer(anchor.get('page_size'), 'anchor.page_size') != 96 or integer(anchor.get('descriptor_offset'), 'anchor.descriptor_offset') != 80:
        raise ValueError('unsupported native page/descriptor ABI')
    octets(anchor.get('descriptor_bytes'), 'anchor.descriptor_bytes', 64)
    events = object_field(p, 'events')
    events['message_id_offset'] = integer(events.get('message_id_offset'), 'events.message_id_offset', 0, 4096)
    if events['message_id_offset'] % 4:
        raise ValueError('unaligned message ID')
    for key in ('startup_complete', 'shutdown'):
        events[key] = integer(events.get(key), 'events.'+key, 0, 0xfffffffe)
    if events['startup_complete'] == events['shutdown']:
        raise ValueError('startup and shutdown events must differ')
    p['allocate_pool'] = integer(p.get('allocate_pool'), 'allocate_pool', 0, 0xffffffff)
    task = object_field(p, 'task')
    for key in ('attributes', 'priority', 'processor', 'affinity'):
        task[key] = integer(task.get(key), 'task.'+key, 0, 0xffffffff)
    task['stack_bytes'] = integer(task.get('stack_bytes'), 'task.stack_bytes', 16384, 1024*1024)
    task['delay_ticks'] = integer(task.get('delay_ticks'), 'task.delay_ticks', 1, 1000)
    if task['stack_bytes'] % 16:
        raise ValueError('task stack must be 16-byte aligned in size')
    hooks = object_field(p, 'hooks')
    used = []
    for name in HOOKS:
        h = object_field(hooks, name)
        h['address'] = integer(h.get('address'), 'hooks.'+name+'.address', app['base'], app['base']+MAIN_BYTES-16)
        h['continuation'] = integer(h.get('continuation'), 'hooks.'+name+'.continuation')
        if h['address'] % 4 or h['continuation'] != h['address']+16:
            raise ValueError('unsupported hook window/continuation: '+name)
        window = octets(h.get('original'), 'hooks.'+name+'.original', 16)
        copyable(window)
        begin, end = h['address'], h['address']+16
        if begin < code['end'] and code['begin'] < end:
            raise ValueError('hook overlaps the code reservation')
        if any(begin < b and a < end for a, b in used):
            raise ValueError('hook windows overlap')
        used.append((begin, end))
    return p, raw


def validate_report(path, raw):
    report = json.loads(Path(path).read_text(encoding='utf-8'))
    if (report.get('schema') != 'fenestra.z8-diag-validation/v1' or
        report.get('target') != 'z8-c311' or report.get('passed') is not True or
        report.get('bindings_sha256') != sha(raw) or report.get('application_sha256') != MAIN_SHA256):
        raise ValueError('independent native validation identity or result differs')
    checks = report.get('checks', {})
    if not isinstance(checks, dict) or any(checks.get(key) is not True for key in VALIDATION_CHECKS):
        raise ValueError('independent native validation is incomplete: '+', '.join(VALIDATION_CHECKS))
    return report


def generate(p, out):
    constants = {'FX_Z8_RAM_BEGIN': p['ram']['begin'], 'FX_Z8_RAM_END': p['ram']['end'],
                 'FX_Z8_ALLOCATE_POOL': p['allocate_pool']}
    for group in ('native', 'data', 'events', 'task'):
        for key, value in p[group].items():
            constants['FX_Z8_'+group.upper()+'_'+key.upper()] = value
    for key in ('group', 'id', 'descriptor'):
        constants['FX_Z8_ANCHOR_'+key.upper()] = p['anchor'][key]
    lines = ['/* Generated from local Z8 binding evidence; never a portable ABI proof. */']
    for key, value in sorted(constants.items()):
        lines.append('#define '+key+' '+(hex(value)+'ull' if value > 0xffffffff else str(value)+'u'))
    values = octets(p['anchor']['descriptor_bytes'], 'anchor.descriptor_bytes', 64)
    lines.append('#ifdef FX_Z8_DEFINE_DESCRIPTOR_BYTES')
    lines.append('static const unsigned char fx_z8_original_descriptor[64]={'+','.join(map(str, values))+'};')
    lines.append('#endif')
    (out/'z8_diag_bindings.h').write_text('\n'.join(lines)+'\n', encoding='ascii')
    for name, (_, label) in HOOKS.items():
        h = p['hooks'][name]
        data = octets(h['original'], 'hooks.'+name+'.original', 16)
        text = '.byte '+','.join(map(str, data))+'\nldr x16,1f\nbr x16\n.balign 8\n1: .quad '+hex(h['continuation'])+'\n'
        (out/(label+'.inc')).write_text(text, encoding='ascii')


def build(bindings, output, firmware=None, validation=None, llvm_bin=None):
    p, raw = load_bindings(bindings)
    out = Path(output).resolve()
    if not out.is_relative_to(ROOT/'build') or out.exists():
        raise ValueError('new owned output directory below build/ required')
    if bool(firmware) != bool(validation):
        raise ValueError('--firmware and --validation must be supplied together for application output')
    native_validation = validate_report(validation, raw) if validation else None
    original = Path(firmware).read_bytes() if firmware else None
    base = p['application']['base']
    if original is not None:
        if len(original) != MAIN_BYTES or sha(original) != MAIN_SHA256:
            raise ValueError('firmware input is not the pinned Z8 C3.11 MAIN without its CRC')
        if any(original[p['code']['begin']-base:p['code']['end']-base]):
            raise ValueError('validated RX reservation is not empty in original MAIN')
        for name in HOOKS:
            h = p['hooks'][name]; at = h['address']-base
            if original[at:at+16] != bytes.fromhex(h['original']):
                raise ValueError('original hook preimage differs: '+name)
    llvm = Path(llvm_bin) if llvm_bin else LLVM_DEFAULT
    def tool(name):
        direct = llvm/(name+'.exe')
        candidate = str(direct) if direct.is_file() else shutil.which(name)
        if not candidate:
            raise RuntimeError('LLVM tool required: '+name)
        return candidate
    cc, ld, objcopy, nm = [tool(n) for n in ('clang', 'ld.lld', 'llvm-objcopy', 'llvm-nm')]
    out.mkdir(parents=True)
    (out/'bindings.input.json').write_bytes(raw)
    generate(p, out)
    if validation:
        (out/'native-validation.input.json').write_bytes(Path(validation).read_bytes())
    generated = [(out/n).relative_to(ROOT).as_posix() for n in
                 ['bindings.input.json', 'z8_diag_bindings.h', *[label+'.inc' for _, label in HOOKS.values()]]]
    initial_inputs = sorted(set([*SOURCES, 'host/build_z8_diag.py', 'host/compiler_dependencies.py',
        'emulation/z8_diag.ld', 'src/platform/z8_c311/diag.h', *generated,
        *[x.relative_to(ROOT).as_posix() for x in (ROOT/'include').rglob('*.h')]]))
    compiler_version = subprocess.check_output([cc, '--version'], text=True)
    identity_header = (out/'z8_diag_identity.h').relative_to(ROOT).as_posix()
    identity_config = {}
    def emit_identity(inputs):
        nonlocal identity_config
        identity, identity_config = identity_configuration(ROOT, out, inputs, {
            'kind': 'source-and-build-configuration/v2', 'target': 'z8-c311',
            'application': MAIN_SHA256, 'compiler': compiler_version,
            'capabilities': 259, 'flags': 6, 'code': p['code'],
            'flags_c': ['-O2', '-ffreestanding', '-mgeneral-regs-only', '-mstrict-align']})
        def array(value):
            return ','.join(str(b) for b in value)
        (out/'z8_diag_identity.h').write_text(
            'static const unsigned char fx_z8_debug_build[32]={'+array(identity)+'};\n'+
            'static const unsigned char fx_z8_debug_application[32]={'+array(bytes.fromhex(MAIN_SHA256))+'};\n', encoding='ascii')
        return identity
    identity = emit_identity(initial_inputs)
    objects, commands, graph, compilations = [], [], {}, {}
    def run(command):
        subprocess.run(command, cwd=ROOT, check=True, timeout=60)
        commands.append(command)
    for i, source in enumerate(SOURCES):
        obj = out/(str(i)+'.o'); dep = obj.with_suffix('.d'); objects.append(str(obj))
        flags = ['--target=aarch64-none-elf', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                 '-Wconversion', '-Wshadow', '-ffreestanding', '-fno-builtin', '-mgeneral-regs-only',
                 '-mstrict-align', '-fno-stack-protector', '-fno-unwind-tables',
                 '-fno-asynchronous-unwind-tables', '-ffunction-sections', '-fdata-sections', '-DFX_FREESTANDING=1',
                 '-I', 'include', '-I', 'src/platform/z8_c311', '-I', str(out)]
        if source.endswith('.S'):
            flags = ['--target=aarch64-none-elf', '-I', str(out)]
        command = [cc, *flags, '-MMD', '-MF', str(dep), '-MT', 'fx_core', '-c', source, '-o', str(obj)]
        run(command); compilations[source] = command; graph[source] = dependencies(dep, ROOT)
    identity, all_inputs, rebuilt = finalize_identity(initial_inputs, graph, identity_header, identity,
                                                     emit_identity, lambda source: run(compilations[source]))
    elf, binary = out/'core.elf', out/'core.bin'
    keep = [symbol for symbol, _ in HOOKS.values()]+['fx_z8_diag_metadata']
    run([ld, '-m', 'aarch64elf', '--defsym=FX_Z8_CODE_BEGIN='+hex(p['code']['begin']),
         '--defsym=FX_Z8_CODE_END='+hex(p['code']['end']), '-T', 'emulation/z8_diag.ld', '--gc-sections',
         *['--undefined='+name for name in keep], *objects, '-o', str(elf)])
    run([objcopy, '-O', 'binary', str(elf), str(binary)])
    symbols = {parts[2]: int(parts[0], 16) for line in subprocess.check_output([nm, '--defined-only', str(elf)], text=True).splitlines()
               if len(parts := line.split()) == 3}
    code = binary.read_bytes()
    if not 0 < len(code) <= p['code']['end']-p['code']['begin']:
        raise ValueError('compiled extension exceeds the reservation')
    changes = []
    for name, (symbol, label) in HOOKS.items():
        h = p['hooks'][name]; start = symbols[label]-p['code']['begin']
        if code[start:start+16] != bytes.fromhex(h['original']):
            raise ValueError('linked displaced window differs: '+name)
        # LDR x16, literal; BR x16; aligned continuation address. The original
        # function-entry ABI and x16 liveness remain the independent report's job.
        load, branch_word = struct.unpack_from('<II', code, start+16)
        literal = start+16+(((load >> 5) & 0x7ffff) << 2)
        if load & 0xff00001f != 0x58000010 or branch_word != 0xd61f0200 or struct.unpack_from('<Q', code, literal)[0] != h['continuation']:
            raise ValueError('linked trampoline continuation differs: '+name)
        delta = symbols[symbol]-h['address']
        if delta % 4 or not -(1 << 27) <= delta < (1 << 27):
            raise ValueError('hook branch outside AArch64 direct range')
        after = struct.pack('<I', 0x14000000 | ((delta//4) & 0x3ffffff))
        changes.append({'hook': name, 'address': hex(h['address']), 'symbol': symbol,
                        'before': h['original'][:8], 'after': after.hex(),
                        'window_sha256': sha(bytes.fromhex(h['original']))})
    result = {'schema': 'fenestra.z8-diagnostic-core/v1', 'target': 'z8-c311',
              'application_bytes': MAIN_BYTES, 'base_sha256': MAIN_SHA256, 'bindings_sha256': sha(raw),
              'capabilities': 259, 'capability_names': ['status', 'echo', 'identity'],
              'code_begin': hex(p['code']['begin']), 'code_bytes': len(code), 'code_sha256': sha(code),
              'symbols': symbols, 'changes': changes, 'native_validation_passed': native_validation is not None,
              'application_image_written': False, 'activated': False, 'qemu_qualified': False,
              'physical_qualified': False, 'flashable': False,
              'debug_build_identity': identity.hex(), 'debug_identity_header': identity_header,
              'debug_identity_configuration': identity_config, 'debug_identity_rebuilt': rebuilt,
              'compiler_dependencies': graph, 'commands': commands,
              'source_sha256': {name: sha((ROOT/name).read_bytes()) for name in sorted(set(all_inputs))}}
    if original is not None:
        candidate = bytearray(original); offset = p['code']['begin']-base
        candidate[offset:offset+len(code)] = code
        for change in changes:
            at = int(change['address'], 16)-base; candidate[at:at+4] = bytes.fromhex(change['after'])
        restored = bytearray(candidate); restored[offset:offset+len(code)] = bytes(len(code))
        for change in changes:
            at = int(change['address'], 16)-base; restored[at:at+4] = bytes.fromhex(change['before'])
        if restored != original or candidate[:0x40000] != original[:0x40000]:
            raise ValueError('unaccounted MAIN or boot-prefix mutation')
        candidate_path = out/'z8-diag.main'
        candidate_path.write_bytes(candidate)
        result.update({'application_image_written': True, 'candidate_sha256': sha(candidate),
                       'candidate_file': candidate_path.name, 'original_reconstruction_equal': True,
                       'boot_prefix_unchanged': True, 'native_validation_sha256': sha(Path(validation).read_bytes())})
    (out/'build.json').write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bindings', required=True, help='complete Z8-specific profile; see bindings.example.json')
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--firmware', help='pinned MAIN without component CRC; requires --validation')
    parser.add_argument('--validation', help='separate fenestra.z8-diag-validation/v1 report')
    parser.add_argument('--llvm-bin')
    args = parser.parse_args()
    result = build(args.bindings, args.output_dir, args.firmware, args.validation, args.llvm_bin)
    print(json.dumps({key: result[key] for key in ('target', 'code_bytes', 'code_sha256',
          'application_image_written', 'native_validation_passed', 'flashable')}))


if __name__ == '__main__':
    main()
