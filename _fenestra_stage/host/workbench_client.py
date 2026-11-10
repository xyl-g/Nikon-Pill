"""Call the open workbench; never opens USB or creates a camera session."""
import argparse
import json
from pathlib import Path
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener
import uuid

ROOT = Path(__file__).resolve().parents[1]


class WorkbenchClient:
    def __init__(self, discovery=None):
        self.config = json.loads(Path(discovery or ROOT / '.workbench/bridge.json').read_text(encoding='utf-8'))
        # Bypass system HTTP proxies for a local authenticated endpoint.
        from urllib.parse import urlsplit
        parsed = urlsplit(self.config['url'])
        if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port or parsed.path or parsed.query or parsed.fragment or parsed.username:
            raise ValueError('Workbench discovery must name a loopback HTTP endpoint')
        self.opener = build_opener(ProxyHandler({}))

    def request(self, method, route, body=None):
        data = json.dumps(body).encode('utf-8') if body is not None else None
        req = Request(self.config['url'] + route, data=data, method=method,
                      headers={'Authorization': 'Bearer ' + self.config['token'], 'Content-Type': 'application/json'})
        with self.opener.open(req, timeout=5) as response:
            return json.load(response)

    def submit(self, action, arguments=None, identifier=None):
        return self.request('POST', '/v1/requests',
                            dict(id=identifier or uuid.uuid4().hex, action=action, arguments=arguments or {}))

    def result(self, identifier):
        return self.request('GET', '/v1/requests/' + identifier)

    def wait(self, identifier, seconds=30):
        end = time.monotonic() + seconds
        while True:
            result = self.result(identifier)
            if result['status'] in ('completed', 'failed'):
                return result
            if time.monotonic() >= end:
                return result  # Retain id; do not submit the operation again.
            time.sleep(.1)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', help='workbench_status, connect, execute, memory_read, result, info, ...')
    p.add_argument('--args', default='{}', help='JSON arguments for a workbench operation')
    p.add_argument('--id', help='stable request id, also used for result lookup')
    p.add_argument('--wait', type=float, default=30, help='wait at most 0..55 seconds; a timeout never repeats the command')
    p.add_argument('--discovery', type=Path)
    a = p.parse_args(argv)
    if not 0 <= a.wait <= 55:
        p.error('--wait must be 0..55 seconds')
    try:
        client = WorkbenchClient(a.discovery)
        if a.action == 'info':
            result = client.request('GET', '/v1/info')
        elif a.action == 'result':
            if not a.id:
                p.error('result requires --id')
            result = client.wait(a.id, a.wait)
        else:
            result = client.submit(a.action, json.loads(a.args), a.id)
            result = client.wait(result['id'], a.wait)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result.get('status') == 'failed' else 0
    except (OSError, ValueError, HTTPError, URLError) as exc:
        print('Workbench request failed: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
