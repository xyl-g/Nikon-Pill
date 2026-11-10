"""Loopback command mailbox. Only the visible Tk window dispatches USB work."""
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
from pathlib import Path
import queue
import secrets
import threading
from urllib.parse import parse_qs, urlsplit
import uuid

from z8_debug_session import DebugWorker, ROOT


# Keep the interface at the same operation level as the visible workbench.
SCHEMAS = {name: {} for name in DebugWorker.ACTIONS}
SCHEMAS.update({
    'workbench_status': {},
    'execute': {'line': (str, 4096), 'timeout_ms': (int, 100, 300000)},
    'native_call': {'address': (int, 1, 2**64 - 1), 'args': (list, 8),
                    'timeout_ms': (int, 100, 300000)},
    'card_upload': {'source': (str, 4096), 'remote': (str, 128), 'backup': (str, 128),
                    'base_source': ((str, type(None)), 4096)},
    'card_probe': {'remote': (str, 128), 'expect': ((str, type(None)), 4096)},
    'memory_write': {'address': (int, 0, 2**64 - 1), 'data_hex': (str, 2016)},
    'memory_read': {'address': (int, 0, 2**64 - 1), 'count': (int, 1, 1008)},
    'camera_control': {'op': (str, 16), 'value': (int, 0, 2**32 - 1)},
    'memory_dump': {'address': (int, 0, 2**64 - 1), 'count': (int, 1, 16*1024*1024), 'output': (str, 4096)},
    'linux_start': {'agent': (str, 4096)},
    'linux_execute': {'line': (str, 4096)},
    'linux_download': {'remote': (str, 4096), 'output': (str, 4096)},
    'linux_upload': {'source': (str, 4096), 'remote': (str, 4096)},
})
DEFAULTS = {'execute': {'timeout_ms': 5000},
            'native_call': {'args': [], 'timeout_ms': 5000},
            'memory_read': {'count': 256}, 'card_upload': {'base_source': None}, 'card_probe': {'expect': None},
            'camera_control': {'value': 0}}


def validate(action, arguments):
    if not isinstance(action, str) or action not in SCHEMAS or not isinstance(arguments, dict):
        raise ValueError('Unknown workbench action or invalid arguments')
    values = {**DEFAULTS.get(action, {}), **arguments}
    schema = SCHEMAS[action]
    if set(values) != set(schema):
        raise ValueError('Arguments must match: ' + ', '.join(schema))
    for name, rule in schema.items():
        value = values[name]
        if isinstance(rule[0], tuple):
            if value is None:
                continue
            if type(value) is not str or not value.strip() or len(value) > rule[1] or '\0' in value:
                raise ValueError('Invalid optional string argument: ' + name)
            continue
        if type(value) is not rule[0]:
            raise ValueError('Invalid argument type: ' + name)
        if rule[0] is str:
            if not value.strip() or len(value) > rule[1] or '\0' in value:
                raise ValueError('Invalid string argument: ' + name)
        elif rule[0] is list:
            if len(value) > rule[1] or any(type(item) is not int or not 0 <= item < 2**64 for item in value):
                raise ValueError('Expected at most 8 uint64 arguments: ' + name)
        elif not rule[1] <= value <= rule[2]:
            raise ValueError('Argument out of range: ' + name)
    if action == 'card_upload':
        from card_upload import card_paths
        card_paths(values['remote'], values['backup'])
    if action == 'card_probe':
        from card_upload import REMOTE
        if not REMOTE.fullmatch(values['remote']):
            raise ValueError(r'remote must be A/B:\FENESTRA\ASCIIid.wlm')
    return values


class BridgeError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class WorkbenchBridge:
    """HTTP threads only enqueue; Tk owns execution, display and completion."""
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory else ROOT / '.workbench'
        self.directory.mkdir(parents=True, exist_ok=True)
        self.session = uuid.uuid4().hex
        self.token = secrets.token_urlsafe(32)
        self.discovery = self.directory / 'bridge.json'
        logs = self.directory / 'sessions'
        logs.mkdir(exist_ok=True)
        self.audit_path = logs / (datetime.now().strftime('%Y%m%d-%H%M%S-') + self.session + '.jsonl')
        self.lock = threading.RLock()
        self.inbox = queue.Queue(maxsize=32)
        self.requests = {}
        self.events = deque(maxlen=2000)
        self.sequence = 0
        self.active = None
        self.closed = False
        self.state = {'connected': False}
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
        self.server.daemon_threads = True
        self.server.bridge = self
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)
        self.thread = threading.Thread(target=self.server.serve_forever, name='Fenestra-workbench-API', daemon=True)
        self.thread.start()
        record = dict(schema='fenestra.workbench/v1', url=self.url, token=self.token,
                      pid=os.getpid(), session=self.session, audit_log=str(self.audit_path))
        temporary = self.discovery.with_suffix('.' + self.session + '.tmp')
        temporary.write_text(json.dumps(record, indent=2), encoding='utf-8')
        temporary.chmod(0o600)
        temporary.replace(self.discovery)
        self.record('workbench_started', source='workbench', details={'url': self.url})

    def record(self, kind, **details):
        with self.lock:
            self.sequence += 1
            event = dict(sequence=self.sequence, time=datetime.now(timezone.utc).isoformat(),
                         session=self.session, kind=kind, **details)
            # Audit before exposing the request to the executor. A log write
            # failure prevents an unlogged request from reaching the camera.
            with self.audit_path.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(event, ensure_ascii=False) + '\n')
            self.events.append(event)
            return event

    def enqueue(self, body):
        if not isinstance(body, dict) or set(body) != {'id', 'action', 'arguments'}:
            raise BridgeError(400, 'Expected id, action and arguments')
        identifier, action = body['id'], body['action']
        if not isinstance(identifier, str) or not 1 <= len(identifier) <= 80 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in identifier):
            raise BridgeError(400, 'Invalid request id')
        try:
            arguments = validate(action, body['arguments'])
        except ValueError as exc:
            raise BridgeError(400, str(exc)) from exc
        with self.lock:
            if self.closed:
                raise BridgeError(503, 'Workbench is closing')
            if identifier in self.requests:
                old = self.requests[identifier]
                if old['action'] != action or old['arguments'] != arguments:
                    raise BridgeError(409, 'Request id already used with different arguments')
                return self.snapshot(identifier)
            if len(self.requests) >= 1024 or self.inbox.full():
                raise BridgeError(429, 'Workbench request capacity reached')
            row = dict(id=identifier, action=action, arguments=arguments, status='queued',
                       output='', output_truncated=False)
            self.record('request', request_id=identifier, source='automation', action=action, arguments=arguments)
            self.requests[identifier] = row
            self.inbox.put_nowait(identifier)
            return dict(row)

    def snapshot(self, identifier):
        with self.lock:
            if identifier not in self.requests:
                raise BridgeError(404, 'Unknown request')
            return dict(self.requests[identifier])

    def take(self):
        with self.lock:
            if self.closed or self.active is not None:
                return None
            try:
                identifier = self.inbox.get_nowait()
            except queue.Empty:
                return None
            try:
                self.record('dispatch', request_id=identifier, source='automation',
                            action=self.requests[identifier]['action'])
            except OSError:
                self.requests[identifier].update(status='failed', error={'error': 'Audit log unavailable; operation not dispatched'})
                raise
            self.active = identifier
            self.requests[identifier]['status'] = 'running'
            return self.snapshot(identifier)

    def observe(self, kind, value):
        with self.lock:
            if kind == 'state':
                self.state = value
            row = self.requests.get(self.active)
            if kind not in ('result', 'error', 'output', 'notice', 'state', 'idle'):
                return
            audit_error = None
            try:
                self.record(kind, request_id=self.active, source='automation' if row else 'gui', value=value)
            except OSError as exc:
                audit_error = exc
            if not row:
                if audit_error:
                    raise audit_error
                return
            if kind == 'output':
                text = value['text']
                capacity = 1024*1024 - len(row['output'])
                row['output'] += text[:capacity]
                row['output_truncated'] |= len(text) > capacity
            elif kind == 'result':
                row['result'] = value['value']
            elif kind == 'error':
                row['error'] = value
            elif kind == 'idle':
                row['status'] = 'failed' if 'error' in row else 'completed'
                row['state'] = self.state
                self.active = None
            if audit_error:
                raise audit_error

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            for row in self.requests.values():
                if row['status'] == 'queued':
                    row['status'] = 'failed'
                    row['error'] = {'error': 'Workbench closed before dispatch'}
            try:
                self.record('workbench_closed', source='workbench')
            except OSError:
                pass  # Still retire the listener when its disk is unavailable.
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        try:
            if json.loads(self.discovery.read_text(encoding='utf-8')).get('session') == self.session:
                self.discovery.unlink()
        except FileNotFoundError:
            pass


class _Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'

    def log_message(self, *args):
        pass

    def _reply(self, code, value):
        payload = json.dumps(value, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(payload)

    def _handle(self, post=False):
        bridge = self.server.bridge
        try:
            expected_host = '127.0.0.1:' + str(self.server.server_port)
            if self.headers.get('Host') != expected_host or self.headers.get('Origin') is not None:
                raise BridgeError(403, 'Only local non-browser clients are supported')
            supplied = self.headers.get('Authorization', '')
            if not hmac.compare_digest(supplied, 'Bearer ' + bridge.token):
                raise BridgeError(401, 'Workbench token required')
            route = urlsplit(self.path)
            if post:
                if route.path != '/v1/requests' or route.query:
                    raise BridgeError(404, 'Unknown route')
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 65536 or self.headers.get('Transfer-Encoding'):
                    raise BridgeError(400, 'Invalid request body length')
                self.connection.settimeout(3)
                result = bridge.enqueue(json.loads(self.rfile.read(length)))
                self._reply(202, result)
            elif route.path == '/v1/info':
                with bridge.lock:
                    result = dict(schema='fenestra.workbench/v1', session=bridge.session,
                                  state=bridge.state, actions=sorted(SCHEMAS), audit_log=str(bridge.audit_path))
                self._reply(200, result)
            elif route.path.startswith('/v1/requests/'):
                self._reply(200, bridge.snapshot(route.path[len('/v1/requests/'):]))
            elif route.path == '/v1/events':
                query = parse_qs(route.query)
                after = int(query.get('after', ['0'])[0])
                with bridge.lock:
                    events = [e for e in bridge.events if e['sequence'] > after][:200]
                    oldest = bridge.events[0]['sequence'] if bridge.events else bridge.sequence + 1
                self._reply(200, dict(events=events, oldest_sequence=oldest,
                                     history_gap=after < oldest-1))
            else:
                raise BridgeError(404, 'Unknown route')
        except BridgeError as exc:
            self._reply(exc.code, {'error': str(exc)})
        except (ValueError, TypeError, TimeoutError) as exc:
            self._reply(400, {'error': str(exc)})
        except Exception:
            self._reply(500, {'error': 'Workbench bridge failure; inspect the local session log'})

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle(True)
