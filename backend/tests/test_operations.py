"""Fail-closed orchestration; never invokes Docker or changes the production DB."""
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest
from app.security.service import verify_after_restore
from app.database.models import Session
from sqlalchemy import select


def test_restore_verification_reports_live_credentials(authenticated, db):
    assert verify_after_restore(db)['valid'] is False
    assert db.scalar(select(Session)) is not None


@pytest.fixture
def operations_module():
    # deploy tests are mounted only in development; image test target copies this source.
    path = Path('/deploy/operations.py')
    if not path.exists():
        path = Path(__file__).resolve().parents[2] / 'deploy' / 'operations.py'
    if not path.exists():
        pytest.skip('Run orchestration tests with deploy sources mounted at /deploy')
    spec = importlib.util.spec_from_file_location('operations', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('failure', ['restore', 'migration', 'invalidation', 'verification', 'health'])
def test_restore_does_not_resume_on_failure(operations_module, tmp_path, failure):
    path = tmp_path / 'fixture.dump'
    path.write_bytes(b'synthetic-backup')
    import hashlib
    path.with_suffix('.dump.json').write_text(json.dumps({'format': 1, 'database': 'hub_test', 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}))
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        joined = ' '.join(args)
        if 'printenv' in args:
            return subprocess.CompletedProcess(args, 0, stdout=b'hub_test\n')
        if '--services' in args:
            return subprocess.CompletedProcess(args, 0, stdout=b'db\napi\nweb\n')
        if (failure == 'restore' and 'pg_restore' in joined or failure == 'migration' and 'alembic' in args
                or failure == 'invalidation' and 'invalidate-after-restore' in args
                or failure == 'health' and 'up' in args):
            raise subprocess.CalledProcessError(1, args)
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps({'valid': failure != 'verification'}).encode())
    with pytest.raises((subprocess.CalledProcessError, ValueError)):
        operations_module.Operations(['fixture.yaml'], run).restore(path, 'hub_test')
    assert any('stop' in call for call in calls)
    if failure == 'health':
        assert 'stop' in calls[-1]
    else:
        assert not any('up' in call for call in calls)


def test_checksum_failure_prevents_restore(operations_module, tmp_path):
    path = tmp_path / 'fixture.dump'
    path.write_bytes(b'synthetic-backup')
    path.with_suffix('.dump.json').write_text(json.dumps({'format': 1, 'sha256': 'wrong'}))
    calls = []
    with pytest.raises(ValueError):
        operations_module.Operations([], lambda *a, **k: calls.append(a)).restore(path, 'hub_test')
    assert calls == []
