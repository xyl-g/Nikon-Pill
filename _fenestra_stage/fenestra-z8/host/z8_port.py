"""Z8 C3.11 research admission: inspect/extract inputs and verify known bindings.

This is not a Fenestra core builder. It does not install firmware, communicate
with a camera or create an update package. Missing platform ABIs remain blocked.
"""
import argparse
import copy
import json
from pathlib import Path

from cipher_patch import atomic_new
import z8_container as container

TARGET = container.TARGET
BASE = int(TARGET['base'], 16)


def verify_binding_fingerprints(application, bindings=None):
    """Check observed original instruction bytes, not semantic ABI equivalence."""
    rows = TARGET['bindings'] if bindings is None else bindings
    checked = []
    for row in rows:
        start = int(row['address'], 16) - BASE
        size = row.get('fingerprint_bytes', 16)
        expected = row.get('fingerprint_sha256')
        if expected is None:
            sample = bytes.fromhex(row['original'])
            size, expected = len(sample), container.sha(sample)
        if start < 0 or size != 16 or start + size > len(application):
            raise ValueError('invalid binding fingerprint range: ' + row['name'])
        if container.sha(application[start:start + size]) != expected:
            raise ValueError('original binding bytes differ: ' + row['name'])
        checked.append(copy.deepcopy(row))
    return checked


def research_profile():
    """Return a partial registry entry; never upgrades a target to runtime-ready."""
    identity = TARGET['identities']['application']
    return dict(model=TARGET['model'], version=TARGET['version'], bytes=identity['bytes'],
                sha256=identity['sha256'], module_identity=identity['sha256'],
                module_name=TARGET['module_name'], runtime_ready=False,
                functions={row['name']: int(row['address'], 16) for row in TARGET['bindings']},
                tables={}, abi_evidence=['targets/nikon-z8-c311.json'],
                blockers=[*TARGET['blockers'], 'The current Z8 diagnostic core cannot load .wlm SDK modules.'])


def register_profile(registry):
    """Opt-in partial profile registration, without changing upstream globals."""
    key = TARGET['target']
    profile = research_profile()
    if key in registry and registry[key] != profile:
        raise ValueError('refusing to replace another Z8 profile')
    registry[key] = profile
    return profile


def verified_profile(application):
    container.verify_application(application)
    profile = research_profile()
    profile['bindings'] = verify_binding_fingerprints(application)
    profile['fingerprints_verified'] = True
    return profile


def bindings(path, *, external_project=None):
    data, _ = container.read_input(path)
    application, receipt = container.application_from_bytes(data, external_project=external_project)
    receipt['profile'] = verified_profile(application)
    receipt['blockers'] = list(TARGET['blockers'])
    receipt['abi_status'] = 'Known Z8 source-recovered subset; instruction fingerprints checked; full Fenestra ABI and hardware behavior remain unqualified.'
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('inspect', 'extract', 'bindings'):
        p = sub.add_parser(name)
        p.add_argument('--firmware', required=True, type=Path)
        p.add_argument('--external-project', type=Path,
                       help='explicit optional local NikonFirmwarePatch project for encrypted input extraction')
        if name == 'extract':
            p.add_argument('--output', required=True, type=Path)
            p.add_argument('--format', choices=('application', 'component'), default='application')
        elif name == 'bindings':
            p.add_argument('--output', type=Path, help='new JSON receipt; never an update package')
    args = parser.parse_args(argv)
    try:
        if args.command == 'inspect':
            result = container.inspect(args.firmware, external_project=args.external_project)
        elif args.command == 'extract':
            result = container.extract(args.firmware, args.output, external_project=args.external_project,
                                       output_kind=args.format)
        else:
            result = bindings(args.firmware, external_project=args.external_project)
        encoded = json.dumps(result, indent=2, sort_keys=True) + '\n'
        if args.command == 'bindings' and args.output is not None:
            atomic_new(args.output, encoded.encode('utf-8'))
        print(encoded, end='')
        return 0
    except (ValueError, OSError, ImportError, AttributeError) as exc:
        parser.exit(2, 'Z8 input rejected: ' + str(exc) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())
