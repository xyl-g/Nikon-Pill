"""Audit plugin Han text against characters used by the exact stock Z8 menu.

This is a conservative vocabulary, not a decoded HIF font cmap or a display
qualification. Scene RECT text has its own rasterized font and is independent.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'host/data/z8-stock-simplified-chinese.json'
TOKEN = re.compile(r'(?P<string>"(?:[^"\\]|\\[\s\S])*")|'
                   r"'(?:[^'\\]|\\[\s\S])*'|/\*[\s\S]*?\*/|//[^\r\n]*")


def load_safe_han(path=FIXTURE):
    row = json.loads(Path(path).read_text(encoding='utf-8'))
    points = row['codepoints']
    if (row['schema'] != 'fenestra.z8-stock-han-vocabulary/v1' or
            row['application_sha256'] != '2b43a31c9de838f84b12416de4348a7004e65be07f853ec56ec2b10f3a8e3853' or
            row['language_index'] != 8 or row['stock_strings'] != 3136 or
            row['han_count'] != 797 or len(points) != 797 or points != sorted(set(points)) or
            any(not 0x3400 <= cp <= 0x9fff for cp in points) or
            hashlib.sha256(struct.pack('<797I', *points)).hexdigest() != row['codepoints_u32le_sha256']):
        raise ValueError('stock Han vocabulary identity differs')
    return frozenset(points)


def unsupported_han(text, safe=None):
    if safe is None:
        safe = load_safe_han()
    return sorted({ord(c) for c in text if is_han(ord(c)) and ord(c) not in safe})


def is_han(cp):
    return (0x3400 <= cp <= 0x9fff or 0xf900 <= cp <= 0xfaff or
            0x20000 <= cp <= 0x323af)


def decode_c_string(token):
    """Read C execution bytes, then require valid UTF-8, including hex escapes."""
    body = token[1:-1]
    raw = bytearray()
    i = 0
    escapes = {'a': 7, 'b': 8, 'f': 12, 'n': 10, 'r': 13, 't': 9,
               'v': 11, '\\': 92, '"': 34, "'": 39, '?': 63}
    while i < len(body):
        c = body[i]
        i += 1
        if c != '\\':
            raw.extend(c.encode('utf-8'))
            continue
        c = body[i]
        i += 1
        if c in escapes:
            raw.append(escapes[c])
        elif c in '\r\n':
            if c == '\r' and body[i:i+1] == '\n':
                i += 1
        elif c == 'x':
            m = re.match(r'[0-9a-fA-F]+', body[i:])
            if not m or int(m[0], 16) > 255:
                raise ValueError('unsupported C byte escape')
            raw.append(int(m[0], 16))
            i += len(m[0])
        elif c in '01234567':
            m = re.match(r'[0-7]{0,2}', body[i:])
            value = int(c + m[0], 8)
            if value > 255:
                raise ValueError('unsupported C octal byte escape')
            raw.append(value)
            i += len(m[0])
        elif c in 'uU':
            n = 4 if c == 'u' else 8
            value = body[i:i+n]
            if len(value) != n or not re.fullmatch(r'[0-9a-fA-F]+', value):
                raise ValueError('invalid C universal character')
            raw.extend(chr(int(value, 16)).encode('utf-8'))
            i += n
        else:
            raise ValueError('unsupported C escape: ' + c)
    return raw.decode('utf-8')


def audit_sources(paths, safe=None):
    if safe is None:
        safe = load_safe_han()
    rows = []
    for path in sorted(set(Path(p).resolve(strict=True) for p in paths)):
        source = path.read_text(encoding='utf-8')
        for match in TOKEN.finditer(source):
            token = match.group('string')
            if token is None:
                continue
            # This text-source audit is deliberately strict: a malformed
            # escaped UTF-8 literal must not silently escape coverage checks.
            text = decode_c_string(token)
            if not any(is_han(ord(c)) for c in text):
                continue
            rows.append(dict(source=str(path.relative_to(ROOT)).replace('\\', '/'),
                             line=source.count('\n', 0, match.start()) + 1,
                             text=text, utf8_bytes_with_nul=len(text.encode('utf-8'))+1,
                             utf16_units_with_nul=len(text.encode('utf-16le'))//2+1,
                             unsupported_han=unsupported_han(text, safe)))
    return rows


def verify_original(path, safe):
    from z8_ui_profile import PROFILE, read_original
    blob = Path(path).read_bytes()
    if hashlib.sha256(blob).hexdigest() != '2b43a31c9de838f84b12416de4348a7004e65be07f853ec56ec2b10f3a8e3853':
        raise ValueError('original Z8 MAIN differs')
    text = PROFILE['text']
    table = struct.unpack_from('<Q', blob, text['table_directory_file'] + 8*8)[0]
    pool = struct.unpack_from('<Q', blob, text['blob_directory_file'] + 8*8)[0]
    offsets = struct.unpack('<3137i', read_original(blob, table, 3137*4))
    decoded = read_original(blob, pool, offsets[-1]).decode('utf-16le')
    points = {ord(c) for c in decoded if 0x3400 <= ord(c) <= 0x9fff}
    if points != safe:
        raise ValueError('stock Han vocabulary does not match original menu')
    return dict(original_sha256=hashlib.sha256(blob).hexdigest(), han_count=len(points), verified=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, action='append', default=[])
    parser.add_argument('--verify-firmware', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--summary', action='store_true')
    args = parser.parse_args()
    safe = load_safe_han()
    rows = audit_sources(args.source, safe)
    report = dict(schema='fenestra.z8-plugin-han-audit/v1',
                  vocabulary=FIXTURE.relative_to(ROOT).as_posix(),
                  source_vocabulary_han_count=len(safe),
                  strings=rows, passed=all(not r['unsupported_han'] for r in rows),
                  scope='Stock-used Han vocabulary only; not full HIF glyph decoding or physical rendering qualification')
    if args.verify_firmware:
        report['original_verification'] = verify_original(args.verify_firmware, safe)
    if args.output:
        args.output.write_text(json.dumps(report, ensure_ascii=True, indent=2)+'\n', encoding='utf-8')
    visible = dict(report, strings=len(rows)) if args.summary else report
    print(json.dumps(visible, ensure_ascii=True, indent=2))
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
