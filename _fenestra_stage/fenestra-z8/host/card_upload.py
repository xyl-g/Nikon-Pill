"""Bounded card installation inside an existing visible-workbench DebugSession.

This module never opens a transport. Uncertain calls deliberately retain every
owned resource and stop; recover() may inspect the original job, never replay it.
resolve() then applies that confirmed result, closes and releases what the
upload owns and moves its temp aside. It never resumes the upload.
"""
import hashlib
import json
from pathlib import Path
import re
import struct

PROFILE = Path(__file__).resolve().parent / 'data/card-native-c311.json'
COLD_RETRY3_ID = 'f06231d84970cda74ff6afcdb2aad7d03f6fe0bc4a3ec76c15a9cfd40654579a'
CHUNK = 32768
ALLOCATION = 65536
MEMORY_CHUNK = 1008
MISSING = 0x80000010
# C3.11 open packs the native volume in bits 16..31 and its table index in
# bits 0..15. The native lookup accepts volumes 0..1 and indices 0..0x1d.
LAST_CARD_VOLUME = 1
LAST_CARD_FILE_INDEX = 0x1d
LIMIT = 16 * 1024 * 1024
REMOTE = re.compile(r'([AB]):\\FENESTRA\\([A-Za-z0-9_-]{1,15})\.wlm', re.ASCII)
BACKUP = re.compile(r'([AB]):\\FENESTRA\\([A-Za-z0-9_-][A-Za-z0-9_.-]{0,95})', re.ASCII)
FUNCTIONS = frozenset(('allocate', 'release', 'open', 'write', 'close', 'rename', 'read', 'sha256', 'cache'))
CREATE = 0x101
ASIDE_LIMIT = 32


class UploadError(RuntimeError):
    pass


class UploadUncertain(UploadError):
    pass


def card_paths(remote, backup):
    """Restrict writes to an ASCII module in the exact root plugin directory."""
    if not isinstance(remote, str) or not (match := REMOTE.fullmatch(remote)):
        raise ValueError(r'remote must be A/B:\FENESTRA\ASCIIid.wlm')
    if not isinstance(backup, str) or not (other := BACKUP.fullmatch(backup)):
        raise ValueError('backup must be an explicit ASCII name in the target directory')
    if match[1] != other[1] or '..' in other[2] or other[2].endswith(('.', '-')):
        raise ValueError('backup must be a new file in the same target directory')
    temp = remote[:-4] + '.uploading'
    if backup.casefold() in (remote.casefold(), temp.casefold()) or backup.lower().endswith('.wlm'):
        raise ValueError('backup must differ from target/temp and must not end in .wlm')
    return remote, temp, backup, match[2]


def load_profile(build_identity=None):
    path = PROFILE.with_name('card-native-c311-cold-retry3.json') if build_identity == COLD_RETRY3_ID else PROFILE
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError as exc:
        raise UploadError('Card upload requires a local, verified native profile for this build') from exc
    if (value.get('schema') != 'fenestra.workbench-card-native-c311/v1' or
            set(value.get('functions', {})) != FUNCTIONS):
        raise ValueError('card native profile schema differs')
    for key in ('build_identity', 'application_sha256', 'code_sha256'):
        if not isinstance(value.get(key), str) or not re.fullmatch('[0-9a-f]{64}', value[key]):
            raise ValueError('card native profile identity differs')
    ram = value.get('native_ram', {})
    if (ram.get('begin'), ram.get('end'), ram.get('alignment')) != (0xa00000000, 0xa40000000, 8):
        raise ValueError('card native RAM range differs from the pinned Z8 worker')
    for entry in value['functions'].values():
        address = entry.get('address')
        if (type(address) is not int or address <= 0 or address & 3 or address >= (1 << 64) - 32 or
                not isinstance(entry.get('hex32'), str) or not re.fullmatch('[0-9a-f]{64}', entry['hex32'])):
            raise ValueError('card native function preimage differs')
    return value


def module_bytes(source, module_id, target):
    path = Path(source)
    if path.suffix.lower() != '.wlm' or not path.is_file() or not 128 < path.stat().st_size <= LIMIT:
        raise ValueError('source must be a bounded local .wlm file')
    # Read at most LIMIT+1 even when a file changes after stat().
    with path.open('rb') as stream:
        data = stream.read(LIMIT + 1)
    if not 128 < len(data) <= LIMIT or data[:8] != b'FENESTRA':
        raise ValueError('source is not a bounded WLM package')
    version = struct.unpack_from('<I', data, 8)[0]
    if version == 4:
        from wlm_bundle import decode_bundle
        decode_bundle(data, module_id=module_id, target=target)
    elif version in (2, 3):
        from module_header import validate_package
        validate_package(data, target)
    else:
        raise ValueError('card upload accepts only service WLM versions 2, 3 or 4')
    return data


def shared_suffix(a, b):
    """Find an equal suffix with whole-block comparisons before the boundary."""
    limit = min(len(a), len(b))
    n = 0
    while n + CHUNK <= limit and a[len(a)-n-CHUNK:len(a)-n] == b[len(b)-n-CHUNK:len(b)-n]:
        n += CHUNK
    while n < limit and a[len(a)-n-1] == b[len(b)-n-1]:
        n += 1
    return n


class CardUpload:
    def __init__(self, session, source, remote, backup, base_source=None):
        self.session = session
        self.remote, self.temp, self.backup, self.module_id = card_paths(remote, backup)
        self.profile = load_profile((session.report or {}).get('build_identity'))
        self.data = module_bytes(source, self.module_id, bytes.fromhex(self.profile['application_sha256']))
        self.source = str(Path(source).resolve())
        self.base = None
        if base_source is not None:
            if not isinstance(base_source, str) or not base_source:
                raise ValueError('base_source must be a local .wlm path when supplied')
            self.base = module_bytes(base_source, self.module_id, bytes.fromhex(self.profile['application_sha256']))
        self.base_source = str(Path(base_source).resolve()) if base_source else None
        self.suffix = shared_suffix(self.data, self.base) if self.base is not None else 0
        if self.suffix <= 65536:
            self.suffix = 0
        self.fresh_state()

    def fresh_state(self):
        self.pointer = None
        self.fds = {}
        self.steps = []
        self.stage = 'validated-local'
        self.inflight = None
        self.retained = False
        self.blocking = True
        self.complete = False
        self.target_exists = False
        self.original_in_backup = False
        self.committed = False
        self.temp_created = False
        self.resolved = False
        self.set_aside = []
        self.uploaded = self.copied = self.verified = self.ram_verified = 0
        self.pending_snapshot = None
        self.recovery = None
        self.retained_client = None
        self.host_sha = hashlib.sha256(self.data).hexdigest()

    def snapshot(self):
        return dict(remote=self.remote, temp=self.temp, backup=self.backup, source=self.source,
            base_source=self.base_source, bytes=len(self.data), newhostSHA=self.host_sha,
            verification='readback-chunk-sha256', stage=self.stage, steps=list(self.steps),
            uploaded_bytes=self.uploaded, reused_bytes=self.copied, verified_bytes=self.verified,
            host_prefix_ram_verified_bytes=self.ram_verified,
            shared_suffix_bytes=self.suffix, allocation=self.pointer, allocation_bytes=ALLOCATION if self.pointer else 0,
            fds={str(fd): dict(value) for fd, value in self.fds.items()}, inflight=self.inflight,
            retained=self.retained, blocking=self.blocking, complete=self.complete,
            target_existed=self.target_exists, original_in_backup=self.original_in_backup,
            committed=self.committed, temp_created=self.temp_created, resolved=self.resolved,
            set_aside=list(self.set_aside), pending=self.pending_snapshot, recovery=self.recovery)

    def event(self, stage, status='running', **details):
        self.stage = stage
        self.steps.append(dict(stage=stage, status=status, **details))
        self.session.emit('output', dict(source='Card', stream=0,
            text=json.dumps(dict(action='card_upload', **self.steps[-1]), ensure_ascii=False) + '\n'))

    def cancel_check(self):
        if self.session.cancel.is_set():
            raise UploadError('card upload cancelled at a confirmed operation boundary')

    def uncertain(self, operation, exc):
        self.retained = self.blocking = True
        self.retained_client = self.session.client
        pending = self.session.client.pending if self.session.client else None
        # Store identities only, never the authentication token or raw protocol frame.
        self.pending_snapshot = dict(rtos_job=self.session.job,
            ptp_request=pending[0] if pending else None, ptp_operation=pending[1] if pending else None,
            client_session=getattr(self.session.client, 'session', None))
        self.event(self.stage, 'uncertain', operation=operation, error=str(exc))
        raise UploadUncertain('card upload stopped with an unconfirmed operation; resources retained, no replay: ' + str(exc)) from exc

    def call(self, function, args):
        self.inflight = dict(kind='native_call', function=function, address=self.profile['functions'][function]['address'], args=list(args))
        try:
            value = self.session.native_call(self.inflight['address'], list(args))
        except Exception as exc:
            self.uncertain(function, exc)
        if (not isinstance(value, dict) or value.get('phase') != 3 or value.get('kind') != 1 or
                type(value.get('result')) is not int or not 0 <= value['result'] < 1 << 64):
            self.uncertain(function, UploadError('native call did not return a confirmed completed result'))
        self.inflight = None
        return value['result']

    def read_memory(self, address, n):
        self.inflight = dict(kind='memory_read', address=address, bytes=n)
        try:
            value = self.session.memory_read(address, n)
            data = bytes.fromhex(value['hex'])
            if value.get('bytes') != n or len(data) != n:
                raise ValueError('short memory read')
        except Exception as exc:
            self.uncertain('memory_read', exc)
        self.inflight = None
        return data

    def write_memory(self, address, data):
        for offset in range(0, len(data), MEMORY_CHUNK):
            self.cancel_check()
            part = data[offset:offset+MEMORY_CHUNK]
            self.inflight = dict(kind='memory_write', address=address+offset, bytes=len(part))
            try:
                value = self.session.memory_write(address+offset, part.hex())
                if value.get('bytes') != len(part):
                    raise ValueError('short memory write')
            except Exception as exc:
                self.uncertain('memory_write', exc)
            self.inflight = None

    def close(self, fd):
        item = self.fds[fd]
        if item['state'] != 'open':
            raise UploadError('file close cannot be replayed')
        item['state'] = 'closing'
        result = self.call('close', [fd]) & 0xffffffff
        if result:
            item['state'] = 'close-failed'
            raise UploadError(f'close failed: 0x{result:08x}')
        del self.fds[fd]

    def open(self, path, mode=0, missing_ok=False):
        pointer = self.path_pointers[path]
        value = self.call('open', [pointer, mode, 0]) & 0xffffffff
        if missing_ok and value == MISSING:
            return None
        if (value >> 16) > LAST_CARD_VOLUME or (value & 0xffff) > LAST_CARD_FILE_INDEX:
            raise UploadError(f'open failed for {path}: 0x{value:08x}')
        if value in self.fds:
            self.uncertain('open', UploadError('open returned an already-owned fd'))
        self.fds[value] = dict(path=path, state='open')
        if path == self.temp and mode == CREATE:
            self.temp_created = True
        return value

    def exists(self, path):
        fd = self.open(path, missing_ok=True)
        if fd is None:
            return False
        self.close(fd)
        return True

    def require_missing(self, path):
        if self.exists(path):
            raise UploadError('refusing to overwrite existing temp/backup: ' + path)

    def move_aside(self, path):
        """Rename a workbench-owned leftover to the first free <path>-oldN name."""
        for n in range(1, ASIDE_LIMIT + 1):
            name = f'{path}-old{n}'
            self.write_memory(self.scratch, name.encode('ascii') + b'\0')
            self.path_pointers[name] = self.scratch
            try:
                if self.exists(name):
                    continue
                self.event('set-aside', source=path, target=name)
                result = self.call('rename', [self.path_pointers[path], self.scratch]) & 0xffffffff
                if result:
                    raise UploadError(f'set-aside rename failed: 0x{result:08x}')
            finally:
                del self.path_pointers[name]
            self.set_aside.append(dict(source=path, target=name))
            self.event('set-aside', 'passed', source=path, target=name)
            return name
        raise UploadError(f'no free set-aside name for {path} after {ASIDE_LIMIT} tries')

    def clear_temp(self):
        # Only this helper creates .uploading files, so an earlier attempt's
        # leftover is kept under a new name instead of blocking every retry.
        if self.exists(self.temp):
            self.move_aside(self.temp)

    def allocate(self):
        self.event('allocate', bytes=ALLOCATION)
        pointer = self.call('allocate', [ALLOCATION, 0])
        if pointer == 0:
            raise UploadError('64 KiB native buffer allocation failed')
        self.adopt_pointer(pointer)
        self.write_paths()
        self.event('allocate', 'passed', address=self.pointer, bytes=ALLOCATION)

    def adopt_pointer(self, pointer):
        self.pointer = pointer
        ram = self.profile['native_ram']
        if (pointer & (ram['alignment']-1) or not ram['begin'] <= pointer <= ram['end']-ALLOCATION or
                any(pointer < item['address']+32 and item['address'] < pointer+ALLOCATION
                    for item in self.profile['functions'].values())):
            self.uncertain('allocate', UploadError('allocation returned a pointer outside pinned native RAM'))
        self.done, self.hash_out = pointer+32768, pointer+32784
        self.path_pointers = {path:pointer+33024+i*512 for i,path in enumerate((self.remote,self.temp,self.backup))}
        self.scratch = pointer+33024+3*512

    def write_paths(self):
        for path, address in self.path_pointers.items():
            self.write_memory(address, path.encode('ascii')+b'\0')

    def owns_allocation(self):
        """True when the three path strings this upload wrote are still intact."""
        # Read through the session: read_memory() would overwrite the record
        # of the interrupted call that resolve() still has to apply.
        for path, address in self.path_pointers.items():
            raw = path.encode('ascii') + b'\0'
            if bytes.fromhex(self.session.memory_read(address, len(raw))['hex']) != raw:
                return False
        return True

    def io(self, function, fd, n):
        if not 1 <= n <= CHUNK:
            raise ValueError('card I/O chunk must be 1..32768')
        self.cancel_check()
        self.write_memory(self.done, bytes(4))
        args = [fd, self.pointer, n, self.done]
        if function == 'write':
            args.append(0)
            # Both host-filled prefixes and adapter memcpy suffixes can be
            # dirty only in CPU caches. The original writer can consume DMA
            # memory: DSB alone does not clean it to the point of coherency.
            # CLEAN preserves partial-line neighbors; never INVALIDATE here.
            self.event('clean-write-buffer', bytes=n)
            cleaned = self.call('cache', [0, self.pointer, n, 4]) & 0xffffffff
            if cleaned:
                raise UploadError(f'write buffer cache CLEAN failed: 0x{cleaned:08x}')
            self.event('clean-write-buffer', 'passed', bytes=n)
        result = self.call(function, args) & 0xffffffff
        if result:
            raise UploadError(f'{function} failed: 0x{result:08x}')
        done = struct.unpack('<I', self.read_memory(self.done, 4))[0]
        if done > n:
            raise UploadError(function + ' returned an invalid completed byte count')
        return done

    def write_file(self, fd, n):
        if self.io('write', fd, n) != n:
            raise UploadError('short write; partial temp retained without retry')

    def upload_prefix(self, fd, count):
        self.event('upload-prefix', 'running' if count else 'passed', done=0, total=count)
        for offset in range(0, count, CHUNK):
            self.cancel_check()
            part = self.data[offset:min(count, offset+CHUNK)]
            self.write_memory(self.pointer, part)
            self.event('verify-host-prefix-ram', offset=offset, bytes=len(part))
            self.call('sha256', [self.pointer, len(part), self.hash_out])
            actual = self.read_memory(self.hash_out, 32)
            if actual != hashlib.sha256(part).digest():
                raise UploadError(f'host prefix RAM chunk SHA256 differs at byte {offset}')
            self.ram_verified += len(part)
            self.event('verify-host-prefix-ram', 'passed', offset=offset, bytes=len(part), sha256=actual.hex())
            self.write_file(fd, len(part))
            self.uploaded += len(part)
            self.event('upload-prefix', 'passed' if self.uploaded == count else 'progress', done=self.uploaded, total=count)

    def copy_suffix(self, source_fd, dest_fd):
        discard = len(self.base) - self.suffix
        for offset in range(0, discard, CHUNK):
            n = min(CHUNK, discard-offset)
            if self.io('read', source_fd, n) != n:
                raise UploadError('short source read while discarding old prefix')
        self.event('copy-suffix', discarded_source_prefix=discard, total=self.suffix)
        for offset in range(0, self.suffix, CHUNK):
            n = min(CHUNK, self.suffix-offset)
            if self.io('read', source_fd, n) != n:
                raise UploadError('short source suffix read')
            self.write_file(dest_fd, n)
            self.copied += n
            self.event('copy-suffix', 'passed' if self.copied == self.suffix else 'progress', done=self.copied, total=self.suffix)

    def verify(self):
        self.event('verify-readback', strategy='readback-chunk-sha256')
        fd = self.open(self.temp)
        for offset in range(0, len(self.data), CHUNK):
            n = min(CHUNK, len(self.data)-offset)
            if self.io('read', fd, n) != n:
                raise UploadError('short temp readback')
            self.call('sha256', [self.pointer, n, self.hash_out])
            actual = self.read_memory(self.hash_out, 32)
            expected = hashlib.sha256(self.data[offset:offset+n]).digest()
            if actual != expected:
                raise UploadError(f'readback chunk SHA256 differs at byte {offset}')
            self.verified += n
            self.event('verify-readback', done=self.verified, total=len(self.data))
        if self.io('read', fd, 1) != 0:
            raise UploadError('temp has extra bytes after expected EOF')
        self.close(fd)
        self.event('verify-readback', 'passed', bytes=self.verified, eof=True)

    def cleanup(self):
        """Called only after confirmed completion; never from a finally block."""
        self.event('cleanup')
        # Cancellation was already observed at a confirmed boundary. Closing
        # owned files must not itself be cooperatively cancelled.
        self.session.cancel.clear()
        first_error = None
        for fd in list(self.fds):
            if self.fds[fd]['state'] != 'open':
                continue
            try:
                self.close(fd)
            except UploadUncertain:
                raise
            except UploadError as exc:
                first_error = first_error or exc
        if self.pointer is not None:
            self.call('release', [self.pointer])
            self.pointer = None
        self.blocking = bool(self.fds)
        self.retained = self.retained or self.blocking
        if first_error:
            raise first_error
        self.event('cleanup', 'retained' if self.fds else 'passed')

    def verify_profile(self):
        report = self.session.report or {}
        if (report.get('build_identity') != self.profile['build_identity'] or
                report.get('application_sha256') != self.profile['application_sha256'] or
                report.get('model') != 'Nikon Z8' or report.get('firmware') != 'C 3.11'):
            raise UploadError('card upload requires the exact pinned Z8 C3.11 build identity')
        self.event('verify-native-profile')
        for name, entry in self.profile['functions'].items():
            self.cancel_check()
            if self.read_memory(entry['address'], 32).hex() != entry['hex32']:
                raise UploadError('native code preimage differs: ' + name)
        self.event('verify-native-profile', 'passed', functions=len(FUNCTIONS))

    def run(self):
        try:
            self.verify_profile()
            self.cancel_check()
            self.allocate()
            self.event('probe-destinations')
            self.clear_temp()
            self.require_missing(self.backup)
            original = self.open(self.remote, missing_ok=True)
            self.target_exists = original is not None
            if original is None:
                self.suffix = 0
            elif not self.suffix:
                self.close(original)
                original = None
            self.event('probe-destinations', 'passed', target_exists=self.target_exists, shared_suffix_bytes=self.suffix)
            self.cancel_check()
            dest = self.open(self.temp, CREATE)
            self.upload_prefix(dest, len(self.data)-self.suffix)
            if self.suffix:
                self.copy_suffix(original, dest)
                self.close(original)
            self.close(dest)
            self.verify()
            self.cancel_check()
            # Probe again immediately before the two explicit rename operations.
            self.require_missing(self.backup)
            if self.target_exists:
                self.event('rename-original-to-backup')
                result = self.call('rename', [self.path_pointers[self.remote], self.path_pointers[self.backup]]) & 0xffffffff
                if result:
                    raise UploadError(f'backup rename failed: 0x{result:08x}')
                self.original_in_backup = True
                self.event('rename-original-to-backup', 'passed')
            self.event('rename-temp-to-target')
            result = self.call('rename', [self.path_pointers[self.temp], self.path_pointers[self.remote]]) & 0xffffffff
            if result:
                raise UploadError(f'target rename failed: 0x{result:08x}; original remains in backup when present')
            self.committed = True
            self.event('rename-temp-to-target', 'passed')
        except UploadUncertain as exc:
            exc.card_upload = self.snapshot()
            raise
        except Exception as exc:
            self.event(self.stage, 'failed', error=str(exc))
            try:
                self.cleanup()
            except Exception as cleanup_error:
                cleanup_error.add_note('Original card upload failure: ' + str(exc))
                cleanup_error.card_upload = self.snapshot()
                raise
            self.event('failed', 'failed', error=str(exc))
            exc.card_upload = self.snapshot()
            raise
        # No cleanup is allowed to run while a native or PTP result is unknown.
        try:
            self.cleanup()
        except Exception as exc:
            exc.card_upload = self.snapshot()
            raise
        self.complete = True
        self.blocking = False
        self.event('complete', 'passed', bytes=len(self.data), newhostSHA=self.host_sha,
            verification='readback-chunk-sha256', original_in_backup=self.original_in_backup)
        return self.snapshot()

    def recovered(self, result):
        self.recovery = result
        self.event(self.stage, 'retained-after-recovery', message='Original job result inspected; upload was not resumed or replayed')

    @classmethod
    def restore(cls, session, snapshot):
        """Rebuild a retained upload from its persisted snapshot, without source bytes."""
        self = cls.__new__(cls)
        self.session = session
        self.remote, self.temp, self.backup, self.module_id = card_paths(snapshot['remote'], snapshot['backup'])
        self.profile = load_profile((session.report or {}).get('build_identity'))
        self.data, self.base = b'', None
        self.source, self.base_source = snapshot.get('source'), snapshot.get('base_source')
        self.suffix = snapshot.get('shared_suffix_bytes', 0)
        self.pointer = None
        self.fds = {int(fd): dict(value) for fd, value in snapshot.get('fds', {}).items()}
        self.steps = list(snapshot.get('steps', []))
        self.stage = snapshot.get('stage', 'restored')
        self.inflight = snapshot.get('inflight')
        self.retained = self.blocking = True
        self.complete = False
        self.target_exists = bool(snapshot.get('target_existed'))
        self.original_in_backup = bool(snapshot.get('original_in_backup'))
        self.committed = bool(snapshot.get('committed'))
        self.temp_created = bool(snapshot.get('temp_created'))
        self.resolved = False
        self.set_aside = list(snapshot.get('set_aside', []))
        self.uploaded = snapshot.get('uploaded_bytes', 0)
        self.copied = snapshot.get('reused_bytes', 0)
        self.verified = snapshot.get('verified_bytes', 0)
        self.ram_verified = snapshot.get('host_prefix_ram_verified_bytes', 0)
        self.pending_snapshot = snapshot.get('pending')
        self.recovery = snapshot.get('recovery')
        self.retained_client = None
        self.host_sha = snapshot.get('newhostSHA')
        if snapshot.get('allocation') is not None:
            self.adopt_pointer(snapshot['allocation'])
        return self

    def recorded_session(self):
        session = (self.pending_snapshot or {}).get('client_session')
        return session if session is not None else (self.recovery or {}).get('session')

    def orphan(self):
        """Native handles of a lost window or an earlier boot are not ours to use."""
        self.event('orphan-native-state', 'passed', fds=sorted(self.fds), allocation=self.pointer,
            message='camera restarted or ownership unproven; native handles dropped, card files still checked')
        self.fds, self.pointer, self.inflight = {}, None, None

    def adopt(self):
        """Apply the recovered result of the call that was in flight when the upload stopped."""
        item = self.inflight
        if item is None:
            return
        if item['kind'] == 'native_call':
            rec = self.recovery
            if (not isinstance(rec, dict) or rec.get('phase') != 3 or rec.get('kind') != 1 or
                    type(rec.get('result')) is not int):
                raise UploadError('the interrupted native call has no recovered completed result')
            job = (self.pending_snapshot or {}).get('rtos_job')
            if job is not None and rec.get('job') != job:
                raise UploadError('recovered job identity differs from the interrupted call')
            self.apply(item['function'], item['args'], rec['result'])
        self.inflight = None
        self.event('adopt-recovered', 'passed', kind=item['kind'], function=item.get('function'),
            result=(self.recovery or {}).get('result') if item['kind'] == 'native_call' else None)

    def apply(self, function, args, result):
        status = result & 0xffffffff
        names = {address: path for path, address in getattr(self, 'path_pointers', {}).items()}
        if function == 'allocate':
            if result:
                self.adopt_pointer(result)
        elif function == 'release':
            self.pointer = None
        elif function == 'open':
            if status != MISSING and (status >> 16) <= LAST_CARD_VOLUME and (status & 0xffff) <= LAST_CARD_FILE_INDEX:
                path = names.get(args[0], 'set-aside probe')
                self.fds[status] = dict(path=path, state='open')
                if path == self.temp and args[1] == CREATE:
                    self.temp_created = True
        elif function == 'close':
            if args[0] in self.fds:
                if status:
                    self.fds[args[0]]['state'] = 'close-failed'
                else:
                    del self.fds[args[0]]
        elif function == 'rename' and not status:
            pair = (names.get(args[0]), names.get(args[1]))
            if pair == (self.remote, self.backup):
                self.original_in_backup = True
            elif pair == (self.temp, self.remote):
                self.committed = True
            elif pair == (self.backup, self.remote):
                self.original_in_backup = False

    def resolve(self):
        """Close and release what a stopped upload owns; never continue it."""
        if not self.blocking or self.complete:
            raise UploadError('no retained card upload to resolve')
        try:
            self.adopt()
            self.session.cancel.clear()
            self.event('resolve')
            for fd in list(self.fds):
                if self.fds[fd]['state'] == 'open':
                    self.close(fd)
            if self.fds:
                raise UploadError('camera did not confirm closing every file; handles retained')
            if self.pointer is None:
                self.allocate()
            else:
                self.write_paths()
            if not self.committed and self.original_in_backup and not self.exists(self.remote):
                self.event('restore-original')
                result = self.call('rename', [self.path_pointers[self.backup], self.path_pointers[self.remote]]) & 0xffffffff
                if result:
                    raise UploadError(f'restoring the original from backup failed: 0x{result:08x}')
                self.original_in_backup = False
                self.event('restore-original', 'passed')
            if not self.committed:
                self.clear_temp()
            self.call('release', [self.pointer])
            self.pointer = None
        except UploadUncertain as exc:
            exc.card_upload = self.snapshot()
            raise
        except Exception as exc:
            self.event('resolve', 'failed', error=str(exc))
            exc.card_upload = self.snapshot()
            raise
        self.blocking = False
        self.resolved = True
        self.event('resolved', 'passed', committed=self.committed, original_in_backup=self.original_in_backup,
            set_aside=list(self.set_aside))
        return self.snapshot()


class CardProbe(CardUpload):
    """Read-only: hash one card file in 32 KiB chunks inside the camera.

    Nothing on the card is created, renamed or written. With an expected local
    WLM the chunk digests are compared with the host's, like verify().
    """
    def __init__(self, session, remote, expect=None):
        if not isinstance(remote, str) or not REMOTE.fullmatch(remote):
            raise ValueError(r'remote must be A/B:\FENESTRA\ASCIIid.wlm')
        self.session = session
        self.remote, self.temp, self.backup, self.module_id = card_paths(remote, remote[:-4] + '.probe')
        self.profile = load_profile((session.report or {}).get('build_identity'))
        self.data = module_bytes(expect, self.module_id, bytes.fromhex(self.profile['application_sha256'])) if expect else b''
        self.source = str(Path(expect).resolve()) if expect else None
        self.base = self.base_source = None
        self.suffix = 0
        self.fresh_state()
        self.committed = True  # resolve() must never touch card files for a probe
        self.result = None

    def run(self):
        try:
            self.verify_profile()
            self.cancel_check()
            self.allocate()
            self.event('probe-file', path=self.remote)
            fd = self.open(self.remote, missing_ok=True)
            result = dict(path=self.remote, exists=fd is not None)
            if fd is not None:
                digests, size = [], 0
                while (n := self.io('read', fd, CHUNK)):
                    self.call('sha256', [self.pointer, n, self.hash_out])
                    digests.append(self.read_memory(self.hash_out, 32))
                    size += n
                    if size > LIMIT:
                        raise UploadError('card file exceeds the 16 MiB probe limit')
                self.close(fd)
                result.update(bytes=size, chunk_digest=hashlib.sha256(b''.join(digests)).hexdigest())
                if self.data:
                    expected = [hashlib.sha256(self.data[i:i+CHUNK]).digest() for i in range(0, len(self.data), CHUNK)]
                    result.update(expected_bytes=len(self.data), expected_sha256=self.host_sha,
                                  matches_expected=digests == expected)
            self.event('probe-file', 'passed', **result)
        except UploadUncertain as exc:
            exc.card_upload = self.snapshot()
            raise
        except Exception as exc:
            self.event(self.stage, 'failed', error=str(exc))
            try:
                self.cleanup()
            except Exception as cleanup_error:
                cleanup_error.add_note('Original card probe failure: ' + str(exc))
                cleanup_error.card_upload = self.snapshot()
                raise
            exc.card_upload = self.snapshot()
            raise
        try:
            self.cleanup()
        except Exception as exc:
            exc.card_upload = self.snapshot()
            raise
        self.complete = True
        self.blocking = False
        self.result = result
        return result
