"""Cross-platform Compose backup/restore. Private dumps never enter build contexts.

Restore is destructive to the configured database: requires an explicit command
and --confirm-database matching POSTGRES_DB. No automatic resume on failures.
"""
import argparse
import hashlib
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


class Operations:
    def __init__(self, files, run=subprocess.run, *, migration_service='api'):
        self.compose = ['docker', 'compose']
        for file in files:
            self.compose += ['-f', file]
        self.run = run
        self.migration_service = migration_service

    def call(self, *args, **kwargs):
        return self.run(self.compose + list(args), cwd=ROOT, check=True, **kwargs)

    def database(self):
        return self.call('exec', '-T', 'db', 'printenv', 'POSTGRES_DB', capture_output=True).stdout.decode().strip()

    def backup(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        # Refuse overwrite; incomplete files cannot be mistaken for verified backups.
        with path.open('xb') as output:
            self.call('exec', '-T', 'db', 'sh', '-c',
                      'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom', stdout=output)
        manifest = {'format': 1, 'database': self.database(), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        path.with_suffix(path.suffix + '.json').write_text(json.dumps(manifest), encoding='utf-8')
        print('Backup and SHA-256 manifest created. Keep both private.')

    def restore(self, path, confirm):
        manifest = json.loads(path.with_suffix(path.suffix + '.json').read_text(encoding='utf-8'))
        if manifest.get('format') != 1 or manifest.get('sha256') != hashlib.sha256(path.read_bytes()).hexdigest():
            raise ValueError('Backup checksum/format mismatch; no restore attempted')
        if self.database() != confirm or manifest.get('database') != confirm:
            raise ValueError('Database confirmation/backup target mismatch; no restore attempted')
        services = self.call('config', '--services', capture_output=True).stdout.decode().splitlines()
        public = [service for service in ('web', 'frontend', 'api') if service in services]
        self.call('stop', *public)
        # From this point EVERY failure leaves the ingress/API stopped.
        with path.open('rb') as source:
            self.call('exec', '-T', 'db', 'sh', '-c',
                      'exec pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --exit-on-error --single-transaction', stdin=source, stderr=subprocess.PIPE)
        self.call('run', '--rm', '--no-deps', self.migration_service, 'alembic', 'upgrade', 'head')
        self.call('run', '--rm', '--no-deps', 'api', 'python', '-m', 'app.cli', 'security', 'invalidate-after-restore')
        verification = self.call('run', '--rm', '--no-deps', 'api', 'python', '-m', 'app.cli', 'security', 'verify-after-restore', capture_output=True)
        if json.loads(verification.stdout).get('valid') is not True:
            raise ValueError('Credential invalidation verification failed')
        try:
            self.call('up', '-d', '--wait', *public)
        except subprocess.CalledProcessError:
            self.call('stop', *public)
            raise
        print('Restore verified; credentials invalidated before enabling application.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--compose-file', action='append', default=[])
    sub = parser.add_subparsers(dest='action', required=True)
    backup = sub.add_parser('backup')
    backup.add_argument('--output', required=True, type=pathlib.Path)
    restore = sub.add_parser('restore')
    restore.add_argument('--input', required=True, type=pathlib.Path)
    restore.add_argument('--confirm-database', required=True)
    args = parser.parse_args()
    operations = Operations(args.compose_file or ['compose.yaml', 'compose.tunnel.yaml'])
    try:
        if args.action == 'backup':
            operations.backup(args.output.resolve())
        else:
            operations.restore(args.input.resolve(), args.confirm_database)
    except (subprocess.CalledProcessError, ValueError, OSError, json.JSONDecodeError):
        print('Operation failed. If restoration began, keep API/ingress stopped; investigate before resuming.', file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
