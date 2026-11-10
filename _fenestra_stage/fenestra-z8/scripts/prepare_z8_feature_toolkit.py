"""Verify and unpack the optional, separately licensed Z8 feature dependency."""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tempfile
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / 'vendor/NikonFirmwarePatch-features.lock.json'
OUTPUT = ROOT / 'vendor/NikonFirmwarePatch-features'
PREFIX = 'NikonFirmwarePatch-v0.4.0-rc.2/'
ARCHIVE_BYTES = 8046605


def sha(data):
    return hashlib.sha256(data).hexdigest()


def prepare(toolkit):
    metadata = json.loads(LOCK.read_bytes())
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise FileExistsError(OUTPUT)
    with toolkit.open('rb') as stream:
        data = stream.read(ARCHIVE_BYTES + 1)
    if len(data) != ARCHIVE_BYTES or sha(data) != metadata['release_toolkit_sha256']:
        raise ValueError('upstream toolkit length or SHA-256 differs')
    with ZipFile(io.BytesIO(data)) as archive, tempfile.TemporaryDirectory(dir=OUTPUT.parent) as temp:
        staged = Path(temp) / OUTPUT.name
        for name, digest in metadata['files'].items():
            relative = PurePosixPath(name)
            if relative.is_absolute() or '..' in relative.parts or '\\' in name or ':' in name:
                raise ValueError(f'unsafe dependency path {name!r}')
            payload = archive.read(PREFIX + name)
            if sha(payload) != digest:
                raise ValueError(f'upstream toolkit member differs: {name}')
            path = staged / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        staged.rename(OUTPUT)
    return len(metadata['files'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('toolkit', type=Path)
    args = parser.parse_args()
    count = prepare(args.toolkit)
    print(f'Installed {count} verified files in {OUTPUT.relative_to(ROOT)}')


if __name__ == '__main__':
    main()
