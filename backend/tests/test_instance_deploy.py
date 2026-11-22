"""Synthetic files/mocked Docker only. Runtime cross-instance tests are separate."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.fixture
def instances():
    source = Path('/deploy/instances.py')
    if not source.exists():
        source = Path(__file__).resolve().parents[2]/'deploy/instances.py'
    sys.path.insert(0, str(source.parent))
    try:
        spec = importlib.util.spec_from_file_location('instance_deploy', source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.path.pop(0)


def pair(module, tmp_path):
    one, two = tmp_path/'personal', tmp_path/'work'
    module.generate(one, 'personal','http://localhost:15473',15473,synthetic=True)
    module.generate(two, 'work','http://127.0.0.1:15474',15474,synthetic=True)
    return one, two


def test_generated_instances_have_no_cross_mounts_or_migrator_in_api(instances, tmp_path):
    one, two = pair(instances,tmp_path)
    spec = instances.preflight(one,two)
    other = instances.preflight(two,one)
    assert spec['project'] != other['project'] and instances.names(spec) != instances.names(other)
    compose = json.loads((one/'compose.json').read_text())
    assert 'ports' not in compose['services']['db'] and compose['networks']['data']['internal']
    assert compose['services']['web']['networks'] == ['edge']
    assert compose['services']['api']['env_file'] == [(one/'private/api.env').as_posix()]
    assert 'MIGRATION_DATABASE_URL' not in instances.read_env(one/'private/api.env')
    assert not (one/'private/api.env').read_text().count('POSTGRES_PASSWORD')
    with pytest.raises(ValueError,match='overwrite'):
        instances.generate(one,'personal','http://localhost:15473',15473,synthetic=True)


def test_proxy_profile_exposes_only_web_and_keeps_previous_profiles_valid(instances, tmp_path):
    legacy, _ = pair(instances, tmp_path)
    old_compose = (legacy / 'compose.json').read_bytes()
    root = tmp_path / 'proxy'
    spec = instances.generate(root, 'personal', 'https://tasks.example.invalid', 15480,
                              synthetic=True, proxy_network='fixture-proxy')
    instances.preflight(root)
    compose = json.loads((root / 'compose.json').read_text())
    assert compose['networks']['proxy'] == {'external': True, 'name': 'fixture-proxy'}
    assert 'ports' not in compose['services']['web']
    assert set(compose['services']['web']['networks']) == {'edge', 'proxy'}
    assert compose['services']['api']['networks'] == ['edge', 'data']
    assert compose['services']['db']['networks'] == ['data']
    assert compose['services']['migrate']['networks'] == ['data']
    assert compose['services']['web']['networks']['proxy']['aliases'] == [
        'personal-hub-' + instances.UUID(spec['instance_id']).hex]
    assert all(compose['services'][s]['restart'] == 'unless-stopped' for s in ('web', 'api', 'db'))
    assert 'restart' not in compose['services']['migrate']
    assert instances.preflight(legacy)
    assert (legacy / 'compose.json').read_bytes() == old_compose


@pytest.mark.parametrize('network', ['host', 'bridge', 'none', '', '../foreign', 'bad network', 123])
def test_unsafe_proxy_network_rejected_before_creating_directory(instances, tmp_path, network):
    root = tmp_path / 'invalid'
    with pytest.raises(ValueError, match='proxy network'):
        instances.generate(root, 'personal', 'https://tasks.example.invalid', 15480,
                           synthetic=True, proxy_network=network)
    assert not root.exists()


def test_real_profiles_accept_immutable_local_image_ids_only(instances, tmp_path):
    root, _ = pair(instances, tmp_path)
    spec = instances.preflight(root)
    spec.update(synthetic=False, origin='https://tasks.example.invalid')
    spec['images'] = {'api': 'sha256:' + 'a' * 64, 'web': 'local/web@sha256:' + 'b' * 64}
    instances.validate_spec(spec, root)
    for value in ('api:latest', 'sha256:abc', 'local/web@sha256:' + 'c' * 63):
        spec['images']['api'] = value
        with pytest.raises(ValueError, match='pinned'):
            instances.validate_spec(spec, root)


@pytest.mark.parametrize('target', ['compose.json','private/api.env','private/migrate.env','private/init.sql','nginx.conf'])
def test_modified_configuration_is_rejected(instances,tmp_path,target):
    one,_ = pair(instances,tmp_path)
    path = one/target
    if target == 'compose.json':
        value = json.loads(path.read_text())
        value['services']['api']['networks'].append('foreign')
        path.write_text(json.dumps(value))
    elif target.endswith('.env'):
        path.write_text(path.read_text()+'FOREIGN_CONFIG=fixture\n')
    else:
        path.write_text(path.read_text()+'unexpected content')
    with pytest.raises(ValueError):
        instances.preflight(one)


def test_peer_same_host_and_shared_key_rejected(instances,tmp_path):
    one,two = pair(instances,tmp_path)
    (two/'private/backup.key').write_bytes((one/'private/backup.key').read_bytes())
    with pytest.raises(ValueError,match='share secrets'):
        instances.preflight(one,two)
    third = tmp_path/'same-host'
    instances.generate(third,'work','http://localhost:15475',15475,synthetic=True)
    with pytest.raises(ValueError,match='overlap'):
        instances.preflight(one,third)


def test_symlink_and_shared_readable_secrets_rejected(instances,tmp_path):
    one,two = pair(instances,tmp_path)
    target = one/'private/backup.key'
    target.unlink()
    target.symlink_to(two/'private/backup.key')
    with pytest.raises(ValueError):
        instances.preflight(one)
    target.unlink()
    target.write_bytes((two/'private/backup.key').read_bytes())
    target.chmod(0o644)
    if sys.platform != 'win32':
        with pytest.raises(ValueError,match='owner-only'):
            instances.preflight(one)


def fake_docker(module, root, calls, fail=None):
    spec = module.preflight(root)
    database = module.names(spec)['database']
    def run(args, **kwargs):
        calls.append(args)
        assert kwargs['env']['COMPOSE_DISABLE_ENV_FILE'] == '1'
        joined = ' '.join(args)
        if 'pg_dump' in joined:
            kwargs['stdout'].write(b'SYNTHETIC dump fixture')
        if fail == 'migration' and 'alembic' in args:
            raise subprocess.CalledProcessError(1,args)
        if 'printenv' in args:
            output = database.encode()
        elif '--services' in args:
            output = b'db\nmigrate\napi\nweb\n'
        else:
            output = b'{"valid":true}'
        return subprocess.CompletedProcess(args,0,stdout=output)
    return run


def test_encrypted_backup_restore_and_stopped_on_migrator_failure(instances,tmp_path):
    one,_ = pair(instances,tmp_path)
    calls=[]
    ops = instances.InstanceOperations(one,fake_docker(instances,one,calls))
    ops.encrypted_backup('fixture.enc')
    assert b'SYNTHETIC' not in (one/'backups/fixture.enc').read_bytes()
    assert not list((one/'private').glob('backup-*'))
    ops.encrypted_restore('fixture.enc',ops.spec['instance_id'],'personal')
    assert any('migrate' in call and 'alembic' in call for call in calls)
    calls.clear()
    ops.run = fake_docker(instances,one,calls,'migration')
    with pytest.raises(subprocess.CalledProcessError):
        ops.encrypted_restore('fixture.enc',ops.spec['instance_id'],'personal')
    assert any('stop' in call for call in calls) and not any('up' in call for call in calls)


def test_cross_instance_tampered_or_corrupt_backup_rejected_before_docker(instances,tmp_path):
    one,two = pair(instances,tmp_path)
    calls=[]
    source = instances.InstanceOperations(one,fake_docker(instances,one,calls))
    target = instances.InstanceOperations(two,fake_docker(instances,two,calls))
    source.encrypted_backup('fixture.enc')
    for name in ('fixture.enc','fixture.enc.json'):
        shutil.copyfile(one/'backups'/name,two/'backups'/name)
    calls.clear()
    with pytest.raises(ValueError):
        target.encrypted_restore('fixture.enc',target.spec['instance_id'],'work')
    assert calls == []
    manifest_path = two/'backups/fixture.enc.json'
    manifest = json.loads(manifest_path.read_text())
    manifest.update(instance_id=target.spec['instance_id'],domain='work',database=instances.names(target.spec)['database'])
    manifest_path.write_text(json.dumps(manifest))
    from cryptography.fernet import InvalidToken
    with pytest.raises(InvalidToken):
        target.encrypted_restore('fixture.enc',target.spec['instance_id'],'work')
    assert calls == []
    with (one/'backups/fixture.enc').open('ab') as stream:
        stream.write(b'corrupt')
    with pytest.raises(ValueError,match='integrity'):
        source.encrypted_restore('fixture.enc',source.spec['instance_id'],'personal')
    assert calls == []


def test_backup_paths_cannot_escape_instance(instances,tmp_path):
    one,_ = pair(instances,tmp_path)
    ops=instances.InstanceOperations(one,lambda *a,**k: None)
    for name in ('../other.enc','/other.enc','..\\other.enc','bad.dump'):
        with pytest.raises(ValueError):
            ops.backup_path(name)


def test_final_restore_identity_failure_stops_ingress(instances,tmp_path,monkeypatch):
    one,_=pair(instances,tmp_path)
    calls=[]
    ops=instances.InstanceOperations(one,fake_docker(instances,one,calls))
    ops.encrypted_backup('fixture.enc')
    checked=0
    def verify():
        nonlocal checked
        checked+=1
        if checked==2:
            raise RuntimeError('Synthetic final identity failure')
    monkeypatch.setattr(ops,'verify',verify)
    with pytest.raises(RuntimeError,match='Synthetic final identity'):
        ops.encrypted_restore('fixture.enc',ops.spec['instance_id'],'personal')
    assert any('up' in c for c in calls)
    assert calls[-1][-3:]==['stop','web','api']


@pytest.mark.parametrize('proxy,drift_kind',
                         [(p, d) for p in (False, True) for d in ('environment','image','mount')]
                         + [(True, d) for d in ('port','api-port','alias','network','restart','logging')])
def test_runtime_verification_rejects_ambient_environment_drift(instances,tmp_path,drift_kind,proxy):
    one,_=pair(instances,tmp_path)
    if proxy:
        one = tmp_path / 'proxy'
        instances.generate(one, 'personal', 'http://localhost:15480', 15480,
                           synthetic=True, proxy_network='fixture-proxy')
    spec=instances.preflight(one)
    project=spec['project']
    drift=False
    def run(args,**kwargs):
        if 'ps' in args:
            output=args[-1].encode()
        elif 'network' in args:
            output=json.dumps([{'Internal':True}]).encode()
        elif 'image' in args:
            output=json.dumps([{'Id':'synthetic-image'}]).encode()
        else:
            service=args[-1]
            env=instances.read_env(one/'private'/f'{service}.env') if service in ('api','db') else {}
            if service=='api' and drift and drift_kind=='environment':
                env['ISOLATION_REQUIRED']='false'
            mounts={'api':[], 'db':[{'Type':'volume','Name':project+'_database'},
                    {'Type':'bind','Destination':'/docker-entrypoint-initdb.d/10-instance.sql','Source':str(one/'private/init.sql'),'RW':False}],
                    'web':[{'Type':'bind','Source':str(one/'nginx.conf'),'Destination':'/etc/nginx/conf.d/default.conf','RW':False}]}[service]
            if service=='web' and drift and drift_kind=='mount':
                mounts[0]['Source']=str(one/'private/init.sql')
            networks={'db':['data'],'api':['edge','data'],'web':['edge']}[service]
            actual_networks = {project+'_'+name:{'IPAddress':'172.20.0.2'} for name in networks}
            if proxy and service == 'web':
                actual_networks['fixture-proxy'] = {'Aliases': ['personal-hub-' + instances.UUID(spec['instance_id']).hex]}
                if drift and drift_kind == 'alias':
                    actual_networks['fixture-proxy']['Aliases'] = ['foreign']
            if proxy and service == 'api' and drift and drift_kind == 'network':
                actual_networks['foreign-data'] = {}
            host_config = {'RestartPolicy': {'Name': 'unless-stopped'},
                           'LogConfig': {'Type': 'json-file', 'Config': {'max-size': '10m', 'max-file': '3'}}}
            if drift and drift_kind == 'restart':
                host_config['RestartPolicy']['Name'] = 'no'
            if drift and drift_kind == 'logging':
                host_config['LogConfig']['Config'] = {}
            expected_image=instances.POSTGRES if service=='db' else spec['images']['web' if service=='web' else 'api']
            output=json.dumps([{'Image':'old-image' if drift and drift_kind=='image' else 'synthetic-image',
                                'Config':{'Env':[f'{k}={v}' for k,v in env.items()], 'Image':expected_image,
                                          'Labels':{'com.docker.compose.project':project}},
                                'HostConfig':host_config,
                                'Mounts':mounts,'NetworkSettings':{'Ports':{'8080/tcp': [{'HostPort': '15480'}]}
                                    if drift and (service == 'web' and drift_kind == 'port'
                                        or service == 'api' and drift_kind == 'api-port') else {'5432/tcp':None},
                                'Networks':actual_networks}}]).encode()
        return subprocess.CompletedProcess(args,0,stdout=output)
    ops=instances.InstanceOperations(one,run)
    assert ops.runtime_resources()['volume']==project+'_database'
    drift=True
    with pytest.raises(ValueError):
        ops.runtime_resources()
