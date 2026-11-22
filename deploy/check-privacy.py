"""Check Git-eligible files for private paths, actual secrets and source UUIDs.

Only counts and filenames are printed. Run locally; no private input is sent.
"""
import pathlib
import re
import subprocess
import sys
root = pathlib.Path(__file__).resolve().parents[1]
git = ['git', '-c', f'safe.directory={root.as_posix()}']
result = subprocess.run(git + ['ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=root, check=True, capture_output=True)
paths = set(result.stdout.decode().split('\0')) - {''}
for forbidden in ['.env', 'data/private-imports/probe.json', 'data/backups/probe.dump', '.runtime/probe.json']:
    if subprocess.run(git + ['check-ignore', '-q', forbidden], cwd=root, capture_output=True).returncode != 0:
        raise SystemExit('Private exclusion missing: ' + forbidden)
needles = set()
env = root / '.env'
if env.exists():
    for line in env.read_text(encoding='utf-8').splitlines():
        if line.startswith(('POSTGRES_PASSWORD=', 'TOTP_ENCRYPTION_KEY=', 'BOOTSTRAP_PASSWORD=')):
            value = line.split('=', 1)[1]
            if len(value) >= 15:
                needles.add(value.encode())
private = root / 'data' / 'private-imports'
if private.exists():
    for path in private.glob('*'):
        if path.is_file() and path.suffix in ('.md', '.json'):
            needles.update(match.encode() for match in re.findall(r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b', path.read_text(encoding='utf-8'), re.I))
failures = []
for name in sorted(paths):
    if name.startswith(('data/private-imports/', 'data/backups/', 'data/exports/', '.runtime/')):
        failures.append(name)
        continue
    path = root / name
    if path.is_file() and any(value in path.read_bytes() for value in needles):
        failures.append(name)
print(f'Privacy: {len(paths)} Git-eligible files checked; {len(failures)} failures')
if failures:
    print('\n'.join(failures))
    sys.exit(1)
