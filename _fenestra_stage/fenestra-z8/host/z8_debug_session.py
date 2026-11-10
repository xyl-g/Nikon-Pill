"""Thread-confined Z8 GUI session. Import/startup never opens a device."""
import codecs
import hashlib
import json
from pathlib import Path
import queue
import struct
import sys
import threading
import time

from ptp_client import Client
from ptp_rtos import RtosDebug
from z8_status import read_status

ROOT = Path(__file__).resolve().parents[1]


def native_transport():
    from ptp_transport import open_native_transport
    return open_native_transport('z8-c311')


def hexdump(data, address=0):
    rows = []
    for offset in range(0, len(data), 16):
        chunk = data[offset:offset + 16]
        visible = ''.join(chr(x) if 32 <= x < 127 else '.' for x in chunk)
        rows.append(f'{address + offset:016x}  {chunk.hex(" "):<47}  {visible}')
    return '\n'.join(rows)


RETAINED_SCHEMA = 'fenestra.workbench-card-retained/v1'


class DebugSession:
    def __init__(self, emit, factory=native_transport, cancel=None, retained_path=None):
        self.emit, self.factory = emit, factory
        # A blocking card upload is persisted here, so a later window or
        # connection can still close and release it instead of forgetting it.
        self.retained_path = Path(retained_path) if retained_path else None
        self.cancel = cancel or threading.Event()
        self.transport = self.client = self.debug = None
        self.report = None
        self.job = None
        self.offset = 0
        self.decoder = codecs.getincrementaldecoder('utf-8')('replace')
        self.bridge = self.linux = self.linux_info = None
        self.linux_job = self.linux_pump = None
        self.linux_decoders = {}
        self.card_transfer = None
        self._card_internal = False
        self.card_orphan = self._load_retained()

    def _load_retained(self):
        if self.retained_path is None or not self.retained_path.is_file():
            return None
        try:
            record = json.loads(self.retained_path.read_text(encoding='utf-8'))
            if record.get('schema') != RETAINED_SCHEMA or not isinstance(record.get('card_upload'), dict):
                raise ValueError('schema differs')
        except (OSError, ValueError) as exc:
            self.emit('notice', f'无法读取保留的卡上传记录 {self.retained_path}：{exc}')
            return None
        return record['card_upload']

    def _persist_card(self):
        if self.retained_path is None:
            return
        transfer = self.card_transfer
        snapshot = transfer.snapshot() if transfer and transfer.blocking else self.card_orphan
        if snapshot is None:
            self.retained_path.unlink(missing_ok=True)
            return
        temp = self.retained_path.with_suffix('.tmp')
        temp.write_text(json.dumps(dict(schema=RETAINED_SCHEMA, card_upload=snapshot), ensure_ascii=False, indent=1),
                        encoding='utf-8')
        temp.replace(self.retained_path)

    def state(self):
        return dict(connected=self.client is not None, report=self.report,
                    pending=bool(self.client and self.client.pending is not None),
                    rtos_job=self.job, linux_ready=self.linux_info is not None,
                    linux_job=self.linux_job,
                    card_upload=self.card_transfer.snapshot() if self.card_transfer else None,
                    card_orphan=self.card_orphan,
                    linux_owned=bool(self.bridge and self.bridge.owns_allocation),
                    linux_pending=bool(self.bridge and
                        (self.bridge.pending is not None or self.bridge.call_pending is not None)))

    def _require(self):
        if self.card_transfer and self.card_transfer.blocking and not self._card_internal:
            raise RuntimeError('卡上传有保留状态；请先查看原请求或恢复原结果，不会自动重发或释放。')
        if self.client is None:
            raise RuntimeError('请先连接 Z8。')
        if self.client.pending is not None:
            raise RuntimeError('上次请求的结果尚未确认，请先恢复结果；不会自动重发命令。')
        if self.bridge and (self.bridge.pending is not None or self.bridge.call_pending is not None):
            raise RuntimeError('Linux 桥有未确认请求，请断开并在机内关闭 USB Debug 后重新连接。')

    def connect(self):
        if self.client is None and self.card_transfer and self.card_transfer.blocking:
            # The old connection is gone. Keep the record; card_resolve proves
            # ownership on the new connection before touching native handles.
            self.card_orphan, self.card_transfer = self.card_transfer.snapshot(), None
            self.job = None  # recorded in the snapshot; it belongs to the old FXP2 session
            self._persist_card()
        if self.client is not None:
            raise RuntimeError('当前已有连接，请先断开。')
        transport = self.factory()
        try:
            transport.__enter__()
            client = Client(transport, on_submit_busy=self._submit_busy)
            report = read_status(client)
        except BaseException:
            try:
                transport.__exit__(*sys.exc_info())
            except Exception:
                pass
            raise
        self.transport, self.client, self.report = transport, client, report
        self.debug = RtosDebug(client)
        return report

    def _submit_busy(self, request_id, operation, delay):
        self.emit('notice', f'相机忙（0x2019 或 WPD 忙），{round(delay * 1000)} ms 后重发同一 FXP2 请求 {request_id}（操作 {operation}）。')

    def disconnect(self):
        notes = []
        retained_card = bool(self.card_transfer and self.card_transfer.blocking)
        if retained_card:
            notes.append('卡上传资源与阶段仍保留；断开不会关闭文件、释放原生缓冲区或重发操作。')
            self.emit('output', dict(source='Card', stream=0, text=json.dumps(self.card_transfer.snapshot(), ensure_ascii=False) + '\n'))
        if self.bridge and self.bridge.owns_allocation:
            try:
                self.linux_stop()
            except Exception as exc:
                notes.append('Linux 代理停止未确认：' + str(exc))
        if self.job is not None:
            notes.append(f'RTOS 任务 {self.job} 的停止未确认；断开连接不会强杀原生代码。')
        transport = self.transport
        self.transport = self.client = self.debug = self.report = None
        self.bridge = self.linux = self.linux_info = None
        self.linux_job = self.linux_pump = None
        if not retained_card:
            self.job = None
        try:
            if transport is not None:
                transport.__exit__(None, None, None)
        finally:
            for note in notes:
                self.emit('notice', note)
        return dict(disconnected=True, notes=notes)

    def refresh(self):
        self._require()
        # Preserve the current FXP2 session and retained job identities.
        raw = self.client.query(1)
        if len(raw) != 32:
            raise ValueError('Z8 status response length differs')
        firmware, active, caps = struct.unpack_from('<3I', raw)
        if firmware != 311 or active not in (0, 1) or caps != self.client.capabilities:
            raise ValueError('Z8 status and identity disagree')
        self.report = dict(self.report, fenestra_active=bool(active), capabilities=caps)
        return self.report

    def echo(self):
        self._require()
        payload = b'Fenestra Z8\x00\xff'
        if self.client.query(2, payload) != payload:
            raise ValueError('USB echo mismatch')
        return dict(echo_verified=True, bytes=len(payload))

    def rtos_status(self):
        self._require()
        return self.debug.job_status()

    def execute(self, line, timeout_ms=5000):
        self._require()
        if self.job is not None or self.linux_job is not None:
            raise RuntimeError('已有保留的调试任务，请先恢复结果。')
        if not line.strip():
            raise ValueError('请输入 RTOS 命令。')
        if not 100 <= timeout_ms <= 300000:
            raise ValueError('超时应为 100..300000 毫秒。')
        self.offset = 0
        self.decoder = codecs.getincrementaldecoder('utf-8')('replace')
        self.job = self.debug.submit_command(line, timeout_ms)
        self.emit('notice', f'RTOS 任务 {self.job} 已提交。')
        return self._wait_rtos(timeout_ms / 1000 + 2)

    def native_call(self, address, args=(), timeout_ms=5000):
        self._require()
        if self.job is not None or self.linux_job is not None:
            raise RuntimeError('已有保留的调试任务，请先恢复结果。')
        if type(address) is not int or not 0 < address < 1 << 64 or address & 3:
            raise ValueError('原生函数地址应为非零、4 字节对齐的 uint64。')
        if not isinstance(args, (list, tuple)) or len(args) > 8 or any(
                type(value) is not int or not 0 <= value < 1 << 64 for value in args):
            raise ValueError('原生调用需要至多 8 个 uint64 参数。')
        if type(timeout_ms) is not int or not 100 <= timeout_ms <= 300000:
            raise ValueError('超时应为 100..300000 毫秒。')
        self.offset = 0
        self.decoder = codecs.getincrementaldecoder('utf-8')('replace')
        # Use this window's existing client. An uncertain POST remains retained
        # by Client.query and recover(); it is never submitted under a new id.
        self.job = self.debug.submit_call(address, args, timeout_ms)
        self.emit('notice', f'原生调用任务 {self.job} 已提交：0x{address:x}。')
        return self._wait_rtos(timeout_ms / 1000 + 2)

    def _rtos_output(self, data):
        self.offset += len(data)
        text = self.decoder.decode(data)
        if text:
            self.emit('output', dict(source='RTOS', stream=0, text=text))

    def _wait_rtos(self, timeout=7):
        self._require()
        job = self.job
        if job is None:
            raise RuntimeError('没有可恢复的 RTOS 任务。')
        deadline = time.monotonic() + timeout
        cancel_sent = False
        while True:
            status = self.debug.job_status()
            if status['job'] != job:
                raise ValueError(f'RTOS job identity changed: expected {job}, got {status["job"]}')
            if status['produced'] < self.offset:
                raise ValueError('RTOS output cursor regressed')
            while self.offset < status['produced']:
                self._rtos_output(self.debug.read_output(job, self.offset,
                    min(1024, status['produced'] - self.offset)))
            if status['phase'] in (3, 4, 5):
                tail = self.decoder.decode(b'', final=True)
                if tail:
                    self.emit('output', dict(source='RTOS', stream=0, text=tail))
                if status['phase'] == 3:
                    self.debug.acknowledge(job)
                    self.job = None
                else:
                    self.emit('notice', '任务已过期或撤销；若 restart_required 为 true，需在机内重启调试通道。')
                return status
            if self.cancel.is_set() and not cancel_sent:
                self.debug.cancel(job)
                cancel_sent = True
                self.emit('notice', '已请求取消；正在执行的原生代码可能仍需自行返回。')
            if time.monotonic() >= deadline:
                raise TimeoutError(f'RTOS 任务 {job} 等待超时，已保留任务和日志游标，可恢复结果。')
            time.sleep(.02)

    def card_upload(self, source, remote, backup, base_source=None):
        self._require()
        if self.job is not None or self.linux_job is not None or (self.bridge and self.bridge.owns_allocation):
            raise RuntimeError('请先处理保留的调试任务并停止 Linux 代理。')
        from card_upload import CardUpload
        transfer = CardUpload(self, source, remote, backup, base_source)
        if self.card_orphan:
            self.emit('notice', '先收尾之前保留的卡上传。')
            self.card_resolve()
        self.card_transfer = transfer
        self._card_internal = True
        try:
            return transfer.run()
        finally:
            # This flag only admits the helper through _require. It never frees
            # native resources or clears an uncertain request.
            self._card_internal = False
            self._persist_card()

    def card_probe(self, remote, expect=None):
        """Read-only check of one card file: existence, size and chunk digests."""
        self._require()
        if self.job is not None or self.linux_job is not None or (self.bridge and self.bridge.owns_allocation):
            raise RuntimeError('请先处理保留的调试任务并停止 Linux 代理。')
        from card_upload import CardProbe
        probe = CardProbe(self, remote, expect)
        self.card_transfer = probe
        self._card_internal = True
        try:
            return probe.run()
        finally:
            self._card_internal = False
            self._persist_card()

    def card_resolve(self):
        """Finish a stopped card upload: apply its recovered result, close, release, set temp aside."""
        if self.client is None:
            raise RuntimeError('请先连接 Z8。')
        transfer = self.card_transfer if self.card_transfer and self.card_transfer.blocking else None
        if transfer is None and not self.card_orphan:
            raise RuntimeError('没有需要收尾的卡上传。')
        self._card_internal = True
        try:
            if self.client.pending is not None or self.job is not None:
                result = self._recover()
                if transfer is not None:
                    transfer.recovered(result)
            if transfer is None:
                transfer = self._claim_orphan()
            self.echo()
            return transfer.resolve()
        finally:
            self._card_internal = False
            self._persist_card()

    def _claim_orphan(self):
        from card_upload import CardUpload
        transfer = CardUpload.restore(self, self.card_orphan)
        recorded, current = transfer.recorded_session(), getattr(self.client, 'session', None)
        # FXP2 session = generation << 32 | hello sequence; both only grow
        # within one boot. Larger now means the camera has not restarted.
        same_boot = type(recorded) is int and type(current) is int and current > recorded
        owned = same_boot and (transfer.pointer is None or transfer.owns_allocation())
        transfer.event('claim-retained', 'passed', recorded_session=recorded, current_session=current,
                       same_boot=same_boot, owned=owned)
        if not owned:
            transfer.orphan()
        elif transfer.inflight and transfer.inflight['kind'] == 'native_call' and not transfer.recovery:
            self._recover_orphan_job(transfer, recorded)
        transfer.retained_client = self.client
        self.card_transfer, self.card_orphan = transfer, None
        return transfer

    def _recover_orphan_job(self, transfer, recorded):
        """Read the lost window's job from the single RTOS job slot, or drop what it may have changed."""
        job = (transfer.pending_snapshot or {}).get('rtos_job')
        status = self.debug.job_status() if job is not None else None
        if status and status['job'] == job and status['session'] == recorded and status['phase'] == 3 and status['kind'] == 1:
            transfer.recovered(status)
            return
        item = transfer.inflight
        if item['function'] == 'close':
            transfer.fds.pop(item['args'][0], None)
        elif item['function'] in ('allocate', 'release'):
            transfer.pointer = None
        transfer.inflight = None
        transfer.event('unrecovered-inflight', 'passed', function=item['function'],
                       message='result of the lost call is unknown; its handle is left to the next camera restart')

    def recover(self):
        self._card_internal = True
        try:
            result = self._recover()
            if self.card_transfer and self.card_transfer.blocking:
                self.card_transfer.recovered(result)
                return dict(recovered_result=result, card_upload=self.card_transfer.snapshot())
            return result
        finally:
            self._card_internal = False
            self._persist_card()

    def _recover(self):
        if self.client is None:
            raise RuntimeError('请先连接 Z8。')
        if self.bridge and (self.bridge.pending is not None or self.bridge.call_pending is not None):
            raise RuntimeError('Linux 有未确认请求，无法自动续接该工作流；请在机内关闭 USB Debug 后重新连接。')
        if self.client.pending is not None:
            _, operation, frame = self.client.pending
            payload = frame[32:]
            result = self.client.fetch()
            if operation == 7 and len(payload) >= 4:
                subop = struct.unpack_from('<I', payload)[0]
                if subop == 5:
                    if len(result) != 4 or not (job := struct.unpack('<I', result)[0]):
                        raise ValueError('RTOS job identifier')
                    self.job, self.offset = job, 0
                    self.decoder = codecs.getincrementaldecoder('utf-8')('replace')
                elif subop == 6:
                    job, offset, count = struct.unpack_from('<III', payload, 4)
                    if job != self.job or offset != self.offset or len(result) != count:
                        raise ValueError('retained RTOS output identity differs')
                    self._rtos_output(result)
                elif subop == 7:
                    job = struct.unpack_from('<I', payload, 4)[0]
                    if result:
                        raise ValueError('RTOS acknowledge schema')
                    if job == self.job:
                        self.job = None
                else:
                    self.emit('notice', '已取回原请求结果：' + result.hex())
            else:
                self.emit('notice', '已取回原请求结果：' + result.hex())
        if self.job is not None:
            return self._wait_rtos()
        if self.linux_job is not None:
            return self._wait_linux()
        return dict(recovered=True)

    def registers(self):
        self._require()
        return self.debug.registers()

    def memory_write(self, address, data_hex):
        self._require()
        if type(address) is not int or not 0 <= address < 1 << 64:
            raise ValueError('uint64 address required')
        if not isinstance(data_hex, str):
            raise ValueError('hexadecimal data required')
        data = bytes.fromhex(data_hex)
        if not 1 <= len(data) <= 1008 or address > (1 << 64) - len(data):
            raise ValueError('write count 1..1008 within uint64 range required')
        count = self.debug.write(address, data)
        return dict(address=address, bytes=count, sha256=hashlib.sha256(data).hexdigest())

    def memory_read(self, address, count=256):
        self._require()
        data = self.debug.read(address, count)
        return dict(address=address, bytes=len(data), hex=data.hex(),
                    display=hexdump(data, address), atomic_snapshot=False)

    def camera_control(self, op, value=0):
        """capture / terminate: Nikon remote release press and release on the card;
        mode_desc / get_mode / set_mode: still capture mode 0x5013 (value written at its read size)."""
        self._require()
        if self.job is not None or self.linux_job is not None:
            raise RuntimeError('已有保留的调试任务，请先恢复结果。')
        control = getattr(self.transport, 'control', None)
        if control is None:
            raise RuntimeError('当前传输不支持相机拍摄控制。')
        if op == 'capture':
            control(0x9207, (0xFFFFFFFF, 0))
            return dict(op=op, started=True)
        if op == 'terminate':
            control(0x920C, (0, 0))
            return dict(op=op, terminated=True)
        if op == 'mode_desc':
            return dict(op=op, hex=control(0x1014, (0x5013,)).hex())
        if op not in ('get_mode', 'set_mode'):
            raise ValueError('op 应为 capture、terminate、mode_desc、get_mode 或 set_mode。')
        raw = control(0x1015, (0x5013,))
        if len(raw) not in (1, 2, 4):
            raise ValueError('拍摄模式属性长度异常')
        current = int.from_bytes(raw, 'little')
        if op == 'get_mode':
            return dict(op=op, value=current, bytes=len(raw))
        if not 0 <= value < 1 << (8 * len(raw)):
            raise ValueError('拍摄模式值超出属性范围')
        control(0x1016, (0x5013,), value.to_bytes(len(raw), 'little'))
        after = int.from_bytes(control(0x1015, (0x5013,)), 'little')
        return dict(op=op, before=current, value=after, applied=after == value)

    def memory_dump(self, address, count, output):
        self._require()
        if not 1 <= count <= 16 * 1024 * 1024:
            raise ValueError('界面内存导出长度应为 1..16 MiB。')
        return self.debug.dump(address, count, output)

    def linux_status(self):
        self._require()
        from ptp_linux import LinuxBridge
        if self.bridge is None:
            self.bridge = LinuxBridge(self.client)
        return self.bridge.status()

    def linux_start(self, agent):
        self._require()
        if self.bridge and self.bridge.owns_allocation:
            raise RuntimeError('当前已有本窗口启动的代理，请先停止。')
        if self.job is not None:
            raise RuntimeError('请先处理保留的 RTOS 任务。')
        from ptp_linux import LinuxBridge
        from linux_debug import LinuxDebug
        self.bridge = LinuxBridge(self.client)
        last = [-1]
        def progress(done, total):
            percent = done * 100 // total
            if percent != last[0]:
                self.emit('progress', percent)
                last[0] = percent
        report = self.bridge.bootstrap(agent, progress)
        self.linux = LinuxDebug(self.bridge.exchange)
        self.linux_info = report['linux']
        return report

    def _require_linux(self):
        self._require()
        if self.linux is None or self.linux_info is None:
            raise RuntimeError('请先选择并启动 Linux 代理。')

    def linux_stop(self):
        self._require()
        if self.linux_job is not None:
            from linux_session import finish_job
            finish_job(self.linux, self.linux_job)
            self.linux_job = self.linux_pump = None
        if self.bridge is not None:
            self.bridge.stop()
        self.linux = self.linux_info = None
        return dict(agent_stopped=True)

    def linux_execute(self, line):
        self._require_linux()
        if self.linux_job is not None or self.job is not None:
            raise RuntimeError('已有保留任务，请先恢复结果。')
        if not line.strip():
            raise ValueError('请输入 Linux 命令。')
        from linux_session import JobPump, require_operations
        require_operations(self.linux_info, 2, 4, 5, 6, 7, 18)
        self.linux_decoders = {s: codecs.getincrementaldecoder('utf-8')('replace') for s in (0, 1)}
        self.linux_job = self.linux.execute(['/bin/sh', '-c', line], 30000)['id']
        self.linux_pump = JobPump(self.linux, self.linux_job, self._linux_output)
        self.linux.close_stdin(self.linux_job)
        return self._wait_linux()

    def _linux_output(self, stream, data):
        text = self.linux_decoders[stream].decode(data)
        if text:
            self.emit('output', dict(source='Linux', stream=stream, text=text))

    def _wait_linux(self):
        self._require_linux()
        end, cancel_sent = time.monotonic() + 33, False
        while True:
            status = self.linux_pump.output()
            if status is not None:
                for stream, decoder in self.linux_decoders.items():
                    text = decoder.decode(b'', final=True)
                    if text:
                        self.emit('output', dict(source='Linux', stream=stream, text=text))
                self.linux.close_job(self.linux_job)
                self.linux_job = self.linux_pump = None
                return status
            if self.cancel.is_set() and not cancel_sent:
                self.linux.cancel(self.linux_job)
                cancel_sent = True
                self.emit('notice', '已请求停止本窗口的 Linux 任务。')
            if time.monotonic() >= end:
                raise TimeoutError('Linux 任务等待超时，任务保留。')
            time.sleep(.02)

    def linux_download(self, remote, output):
        self._require_linux()
        from linux_session import download, require_operations
        require_operations(self.linux_info, 10, 11, 13)
        return download(self.linux, remote, output)

    def linux_upload(self, source, remote):
        self._require_linux()
        from linux_session import upload, require_operations
        require_operations(self.linux_info, 10, 11, 12, 13, 19)
        return upload(self.linux, source, remote)


_COM_IMPORT_LOCK = threading.Lock()


def initialize_worker_com():
    # comtypes initializes the first importing thread automatically. Own exactly
    # one initialization on this worker, whether the module was loaded or not.
    with _COM_IMPORT_LOCK:
        already_imported = 'comtypes' in sys.modules
        import comtypes
        if already_imported:
            comtypes.CoInitialize()
        return comtypes


class DebugWorker:
    """One persistent worker owns COM, transport and all protocol state."""
    ACTIONS = frozenset(('connect', 'disconnect', 'refresh', 'echo', 'rtos_status',
        'execute', 'native_call', 'card_upload', 'card_probe', 'card_resolve', 'recover', 'registers', 'memory_read', 'memory_write', 'memory_dump',
        'camera_control',
        'linux_status', 'linux_start', 'linux_stop', 'linux_execute',
        'linux_download', 'linux_upload'))

    def __init__(self, session_factory=None):
        self.events, self.tasks = queue.Queue(), queue.Queue()
        self.cancel = threading.Event()
        self.session_factory = session_factory
        self.thread = threading.Thread(target=self._run, name='Fenestra-Z8-USB', daemon=True)
        self.thread.start()

    def submit(self, action, **arguments):
        if action not in self.ACTIONS:
            raise ValueError('unknown debug action')
        self.cancel.clear()
        self.tasks.put((action, arguments))

    def stop(self):
        self.tasks.put(None)

    def _emit(self, kind, value):
        self.events.put((kind, value))

    def _run(self):
        session = (self.session_factory(self._emit, self.cancel) if self.session_factory else
                   DebugSession(self._emit, cancel=self.cancel, retained_path=ROOT / '.workbench/card-retained.json'))
        com = None
        try:
            while (task := self.tasks.get()) is not None:
                action, arguments = task
                try:
                    # Importing/constructing the window remains independent of COM.
                    if action == 'connect' and sys.platform == 'win32' and not self.session_factory and com is None:
                        com = initialize_worker_com()
                    result = getattr(session, action)(**arguments)
                    self._emit('result', dict(action=action, value=result))
                except Exception as exc:
                    self._emit('error', dict(action=action, error=str(exc),
                        kind=type(exc).__name__, notes=getattr(exc, '__notes__', []),
                        **({'card_upload': exc.card_upload} if hasattr(exc, 'card_upload') else {})))
                finally:
                    self._emit('state', session.state())
                    self._emit('idle', action)
        finally:
            try:
                session.disconnect()
            except Exception as exc:
                self._emit('notice', '退出时关闭连接失败：' + str(exc))
            if com is not None:
                com.CoUninitialize()
