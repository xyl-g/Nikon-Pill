"""Execute selected original Z8 C3.11 AArch64 ABI paths offline in Unicorn.

Real original instructions run against initialized image data and explicit
synthetic RAM. Named downstream boundaries are intercepted and reported. This
does not run USB hardware, RTOS scheduling, interrupts, MMU or camera firmware.
"""
import argparse
import json
from pathlib import Path
import struct

from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE, UC_HOOK_MEM_UNMAPPED, __version__ as unicorn_version
from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_LR, UC_ARM64_REG_SP, UC_ARM64_REG_PC, UC_ARM64_REG_TPIDR_EL1, UC_ARM64_REG_PSTATE, UC_ARM64_REG_CPACR_EL1
from cipher_patch import atomic_new
import z8_container

BASE = 0x40160000
INIT_FILE, INIT_RAM, INIT_BYTES = 0x4b13000, 0xa02808000, 0x2ad000
BSS_FIRST, BSS_END = INIT_RAM + INIT_BYTES, 0xa04b89000
STACK, SCRATCH, CONTEXT, STOP = 0x60000000, 0x61000000, 0x62000000, 0x63000000
MASK64 = (1 << 64) - 1


def require(value, message):
    if not value:
        raise AssertionError(message)


class OriginalLab:
    def __init__(self, application):
        self.application = z8_container.verify_application(application)
        self.u = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        self.pages = set()
        self.boundaries = {}
        self.boundary_calls = []
        self.instructions = 0
        self.executed = set()
        for address, length in ((STACK, 0x10000), (SCRATCH, 0x20000), (CONTEXT, 0x1000), (STOP, 0x1000)):
            self.u.mem_map(address, length)
            self.pages.update(range(address, address + length, 4096))
        self.u.reg_write(UC_ARM64_REG_TPIDR_EL1, CONTEXT)
        self.u.reg_write(UC_ARM64_REG_CPACR_EL1, 3 << 20)
        self.u.reg_write(UC_ARM64_REG_PSTATE, 5)  # EL1h, DAIF clear; synthetic context.
        self.u.hook_add(UC_HOOK_MEM_UNMAPPED, self._unmapped)
        self.u.hook_add(UC_HOOK_CODE, self._instruction)

    def _page(self, address):
        page = address & ~4095
        if page in self.pages:
            return
        if BASE <= page < BASE + len(self.application):
            data = self.application[page - BASE:page - BASE + 4096]
        elif INIT_RAM <= page < INIT_RAM + INIT_BYTES:
            offset = INIT_FILE + page - INIT_RAM
            data = self.application[offset:offset + 4096]
        elif BSS_FIRST <= page < BSS_END:
            data = bytes(4096)
        else:
            raise AssertionError('unmodeled memory access ' + hex(address))
        self.u.mem_map(page, 4096)
        self.u.mem_write(page, data)
        self.pages.add(page)

    def _unmapped(self, uc, access, address, size, value, user):
        for page in range(address & ~4095, ((address + size - 1) & ~4095) + 1, 4096):
            self._page(page)
        return True

    def read(self, address, length):
        if length:
            for page in range(address & ~4095, ((address + length - 1) & ~4095) + 1, 4096):
                self._page(page)
        return bytes(self.u.mem_read(address, length))

    def write(self, address, data):
        if data:
            for page in range(address & ~4095, ((address + len(data) - 1) & ~4095) + 1, 4096):
                self._page(page)
        self.u.mem_write(address, data)

    def u32(self, address):
        return struct.unpack('<I', self.read(address, 4))[0]

    def u64(self, address):
        return struct.unpack('<Q', self.read(address, 8))[0]

    def w32(self, address, value):
        self.write(address, struct.pack('<I', value & 0xffffffff))

    def w64(self, address, value):
        self.write(address, struct.pack('<Q', value & MASK64))

    def _instruction(self, uc, address, size, user):
        if address == STOP:
            uc.emu_stop()
            return
        if address in self.boundaries:
            name, callback = self.boundaries[address]
            args = [uc.reg_read(UC_ARM64_REG_X0 + i) for i in range(8)]
            self.boundary_calls.append(dict(address=hex(address), name=name, args=[hex(x) for x in args[:5]]))
            result = callback(args)
            uc.reg_write(UC_ARM64_REG_X0, (0 if result is None else result) & MASK64)
            uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_LR))
            return
        require(BASE <= address < BASE + len(self.application), 'execution left original application')
        self.instructions += 1
        self.executed.add(address)

    def intercept(self, address, name, callback=lambda args: 0):
        self.boundaries[address] = (name, callback)

    def call(self, address, *args):
        self.u.reg_write(UC_ARM64_REG_SP, STACK + 0xf000)
        self.u.reg_write(UC_ARM64_REG_LR, STOP)
        for i in range(8):
            self.u.reg_write(UC_ARM64_REG_X0 + i, (args[i] if i < len(args) else 0) & MASK64)
        self._page(address)
        self.u.emu_start(address, 0, count=200000)
        require(self.u.reg_read(UC_ARM64_REG_PC) == STOP, 'original routine exceeded bounded instruction allowance')
        return self.u.reg_read(UC_ARM64_REG_X0)

    def signed32(self, address, *args):
        return struct.unpack('<i', struct.pack('<I', self.call(address, *args) & 0xffffffff))[0]

    def receipt(self, name, **details):
        return dict(name=name, original_instructions_executed=self.instructions,
                    unique_original_addresses=len(self.executed), intercepted_boundaries=self.boundary_calls,
                    **details)


def check_lookup(application):
    lab = OriginalLab(application)
    tables, first = [], {}
    for base in (0xa02984160, 0xa029846a0, 0xa029879e0):
        count = 0
        for index in range(1024):
            address = base + 64 * index
            operation = lab.u32(address)
            if not operation:
                break
            first.setdefault(operation, address)
            count += 1
        else:
            raise AssertionError('native PTP table has no bounded sentinel')
        tables.append(dict(address=hex(base), descriptor_bytes=64, entries=count))
    require(all(t['entries'] for t in tables), 'expected three nonempty stock PTP tables')
    for operation, address in first.items():
        require(lab.call(0x407dea74, operation) == address, 'native PTP lookup returned wrong descriptor')
        require(lab.call(0x407dea74, operation) == address, 'native PTP cached lookup differs')
    unknown = [value for value in (0, 0xdead, 0xffffffff) if value not in first]
    for operation in unknown:
        require(lab.call(0x407dea74, operation) == 0, 'unknown operation was not NULL')
    return lab.receipt('ptp.lookup.stock_tables_and_cache', tables=tables, distinct_operations=len(first),
                       unknown_operations=[hex(x) for x in unknown], calls=2 * len(first) + len(unknown),
                       scope='Real lookup and table/cache instructions; initialized stock descriptor tables.')


def check_ptp_getters(application):
    lab = OriginalLab(application)
    for pointer in (SCRATCH + 0x1000, SCRATCH + 0x1800):
        lab.w64(0xa047ce690, pointer)
        require(lab.call(0x407defe4) == pointer + 12, 'payload getter is not pointer + 12')
    require(lab.call(0x407df020) == 0x1ffff8, 'native payload capacity differs')
    for pointer in (0, SCRATCH + 0x3000):
        lab.w64(0xa031c4430, pointer)
        require(lab.call(0x407df0c8) == pointer, 'incoming getter/thunk differs')
    return lab.receipt('ptp.payload_capacity_incoming', payload_global='0xa047ce690',
                       capacity=0x1ffff8, incoming_global='0xa031c4430',
                       scope='Real getter and incoming thunk instructions; pointer values are synthetic.')


def check_session(application):
    lab = OriginalLab(application)
    notified = []
    lab.intercept(0x407dcfd0, 'native.session.notification', lambda a: notified.append(a[:2]) or 0)
    lab.w32(0xa047ce474, 0)
    for value in (0, 7, 7, 0, 0xffffffff):
        lab.call(0x407dd4a0, value)
        require(lab.u32(0xa047ce474) == value, 'session global not updated')
    require(notified == [[0x4070016, 7], [0x4070016, 0], [0x4070016, 0xffffffff]], 'native session notification suppression differs')
    return lab.receipt('ptp.session_change_notification', transitions=5, notifications=len(notified),
                       scope='Real change detection/global update; downstream notification is a boundary.')


def check_operations(application):
    lab = OriginalLab(application)
    for operations in ([], [0x1001], [0x1001, 0x1002, 0x9999], list(range(0x9200, 0x9208))):
        source, output = SCRATCH + 0x100, SCRATCH + 0x1000
        expected = struct.pack('<I', len(operations)) + b''.join(struct.pack('<H', op) for op in operations)
        lab.write(source, expected)
        lab.write(output, b'\xa5' * (len(expected) + 16))
        result = lab.call(0x40356a74, output, len(expected), source)
        require(result == len(expected), 'operation-array encoded length differs')
        require(lab.read(output, len(expected)) == expected, 'native operation-array bytes differ')
        require(lab.read(output + len(expected), 16) == b'\xa5' * 16, 'operation-array destination overrun')
    return lab.receipt('ptp.operations_u16_array_copy', arrays=4,
                       scope='Real array-size, encoding/copy and original memcpy instructions. Only correctly sized output capacities are tested.')


def check_task_wrappers(application):
    lab = OriginalLab(application)
    block, parameters, entry, argument = SCRATCH + 0x100, SCRATCH + 0x200, SCRATCH + 0x8000, SCRATCH + 0x9000
    descriptor, resume = [], []
    lab.write(block, bytes(64))
    lab.write(parameters, struct.pack('<III', 2, 40, 32769) + b'AbiProbe\0' + bytes(7))
    lab.intercept(0x40185064, 'kernel.task.create.capture_only', lambda a: descriptor.append(lab.read(a[0], 56)) or 7)
    lab.intercept(0x40183490, 'kernel.context.is_irq', lambda a: 0)
    def copy_name(a):
        require(a[0] == block + 0x1d and a[1] == parameters + 0xd, 'task name copy ABI differs')
        name = lab.read(a[1], 16).split(b'\0', 1)[0] + b'\0'
        lab.write(a[0], name)
    lab.intercept(0x40d2bb88, 'native.task.name_copy', copy_name)
    registered = []
    lab.intercept(0x40d2caa8, 'native.task.registry', lambda a: registered.append(a[:3]) or 0)
    lab.intercept(0x40d2d1b8, 'native.task.metadata', lambda a: 0)
    lab.call(0x40d2d498, block, entry, parameters)
    require(len(descriptor) == 1, 'wrapper did not make one kernel create call')
    record = descriptor[0]
    attrs, reserved, context, trampoline, priority, reserved2, stack_bytes, stack, processor, affinity = struct.unpack('<IIQQIIQQII', record)
    require((attrs, context, trampoline, priority, stack_bytes, stack, processor, affinity) ==
            (0, block, 0x40d2d5ac, 40, 32784, 0, 2, 0xffffffff), '56-byte kernel descriptor fields differ')
    require(lab.u32(parameters + 8) == 32784, 'wrapper did not round stack to 16 bytes')
    require(lab.u32(block) == 7 and lab.u64(block + 8) == entry, '64-byte wrapper result differs')
    require(registered == [[0, 7, block]], 'wrapper task registry arguments differ')
    lab.intercept(0x401855e0, 'kernel.task.resume.capture_only', lambda a: resume.append(a[0]) or 0)
    lab.call(0x40d2d5cc, block, argument)
    require(resume == [7] and lab.u64(block + 0x28) == argument, 'wrapper resume ID/context differs')
    return lab.receipt('task.wrapper_create_and_start_boundary', descriptor_bytes=56,
                       descriptor=dict(attributes=attrs, context=hex(context), entry=hex(trampoline), priority=priority,
                                       stack_bytes=stack_bytes, stack=hex(stack), processor=processor, affinity=hex(affinity)),
                       wrapper_bytes=64, created_id=7, resumed_id=7,
                       scope='Real wrapper construction and start instructions; kernel create/resume, context query, registry/name/metadata are explicit boundaries. No task was scheduled.')


def check_kernel_guards(application):
    lab = OriginalLab(application)
    descriptor = SCRATCH + 0x100
    valid = dict(attributes=0, reserved=0, context=SCRATCH + 0x200, entry=SCRATCH + 0x8000,
                 priority=28, reserved2=0, stack_bytes=32768, stack=SCRATCH + 0x10000,
                 processor=1, affinity=0xffffffff)
    fields = list(valid)
    cases = [('attributes', 4, -11), ('stack_bytes', 0, -17), ('priority', 0, -17),
             ('priority', 257, -17), ('entry', 0, -17), ('entry', SCRATCH + 0x8001, -17),
             ('stack', SCRATCH + 0x10001, -17), ('processor', 4, -18), ('affinity', 0, -17)]
    observed = []
    for field, value, expected in cases:
        values = dict(valid, **{field: value})
        lab.write(descriptor, struct.pack('<IIQQIIQQII', *(values[f] for f in fields)))
        result = lab.signed32(0x40185064, descriptor)
        require(result == expected, 'native task create guard differs: ' + field)
        observed.append(dict(field=field, value=value, result=result))
    for invalid in (-1, lab.u32(0xa02aaaab8) + 1):
        require(lab.signed32(0x401855e0, invalid) == -18, 'native resume ID validation differs')
    require(lab.signed32(0x40187b00, 1) == -25, 'native delay with no current task should reject')
    lab.w32(CONTEXT + 0x50, 1)
    for function, args in ((0x40185064, (descriptor,)), (0x401855e0, (1,)), (0x40187b00, (1,))):
        require(lab.signed32(function, *args) == -25, 'kernel IRQ-context guard differs')
    return lab.receipt('kernel.task_argument_and_context_guards', create_argument_cases=observed,
                       invalid_resume_ids=2, delay_without_current_task=1, rejected_irq_contexts=3,
                       scope='Real create/resume/delay validation branches only, with synthetic EL1 thread context. Successful task allocation, context switching and delay timing were not run.')


def check_packet_construction(application):
    lab = OriginalLab(application)
    request, response, payload, transfer = SCRATCH + 0x100, SCRATCH + 0x1000, SCRATCH + 0x2000, SCRATCH + 0x3000
    params = SCRATCH + 0x4000
    lab.write(request, struct.pack('<IHHI', 12, 1, 0x9999, 0x12345678))
    lab.write(params, struct.pack('<6I', 10, 20, 30, 40, 50, 60))
    lab.intercept(0x4034b4b4, 'native.response_buffer', lambda a: response)
    lab.intercept(0x407de308, 'native.ptp.event_signal')
    lab.intercept(0x40857704, 'native.usb.response_commit')
    lab.intercept(0x407dc3e8, 'native.ptp.packet_log')
    for count in (0, 2, 6):
        lab.call(0x407df0d4, request, 0x2001, count, params, 0)
        effective = min(count, 5)
        expected = struct.pack('<IHHI', 12 + 4 * effective, 3, 0x2001, 0x12345678) + lab.read(params, 4 * effective)
        require(lab.read(response, len(expected)) == expected, 'native response packet construction differs')
    lab.w64(0xa047ce690, payload)
    lab.intercept(0x4034b2f0, 'native.transfer_record', lambda a: transfer)
    lab.intercept(0x4034b328, 'native.transfer_prepare')
    lab.intercept(0x40857700, 'native.usb.data_commit')
    lab.intercept(0x407df4c4, 'native.transfer_wait', lambda a: 1)
    require(lab.call(0x407df3c4, request, payload + 16, 24, 5000) == 0, 'send accepted wrong payload pointer')
    require(lab.call(0x407df3c4, request, payload + 12, 24, 5000) == 1, 'bounded send result differs')
    require(lab.read(payload, 12) == struct.pack('<IHHI', 36, 2, 0x9999, 0x12345678), 'native data packet header differs')
    require(lab.read(transfer + 0x10, 16) == struct.pack('<IIQ', 1, 36, payload), 'native transfer record fields differ')
    return lab.receipt('ptp.response_and_data_packet_construction', response_parameter_counts=[0, 2, 6],
                       clamped_response_parameters=5, wrong_payload_rejected=True,
                       scope='Real header/parameter construction, state writes and original memcpy/memset; buffer allocation, event, USB commit, packet log and wait are boundaries. No USB I/O was performed.')


def verify(application):
    z8_container.verify_application(application)
    checks = [check_lookup(application), check_ptp_getters(application), check_session(application),
              check_operations(application), check_task_wrappers(application), check_kernel_guards(application),
              check_packet_construction(application)]
    return dict(schema='fenestra.z8-native-abi-execution/v1', target='z8-c311', runtime_ready=False,
                application_bytes=len(application), application_sha256=z8_container.sha(application),
                engine='Unicorn ' + unicorn_version, software_validation='completed', checks=checks,
                limitations=['No USB hardware or host/device transaction was exercised.',
                             'No RTOS task was successfully allocated, scheduled or context-switched; no delay timing was measured.',
                             'No startup/shutdown hook, MMU mapping, IRQ lifecycle or camera boot was exercised.',
                             'Named downstream boundaries use synthetic return values and are listed per check.',
                             'This report supports only the executed software paths; it does not qualify a flashable firmware.'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firmware', required=True, type=Path, help='exact original Z8 application, without component CRC')
    parser.add_argument('--output', type=Path, help='new JSON software-execution report')
    args = parser.parse_args(argv)
    application, kind = z8_container.read_input(args.firmware)
    if kind != 'application':
        parser.error('provide the extracted original application without the two CRC bytes')
    result = verify(application)
    encoded = json.dumps(result, indent=2, sort_keys=True) + '\n'
    if args.output is not None:
        atomic_new(args.output, encoded.encode('utf-8'))
    print(encoded, end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
