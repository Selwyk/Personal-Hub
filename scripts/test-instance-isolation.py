"""Operational checks only for two new PH-CORE-003 synthetic product profiles.

Requires their one-time fixture owners and browser states from each own smoke.spec.ts.
Never accepts a real profile; no reset, volume removal or external provider call.
"""
import argparse
import base64
import hashlib
import hmac
import http.cookiejar
import importlib.util
import json
from pathlib import Path
import socket
import struct
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPCookieProcessor

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'deploy'))
spec = importlib.util.spec_from_file_location('isolation_instances', ROOT/'deploy/instances.py')
instances = importlib.util.module_from_spec(spec)
spec.loader.exec_module(instances)


class Client:
    def __init__(self, manifest, cookies=()):
        self.origin=manifest['origin']
        self.jar=http.cookiejar.CookieJar()
        for row in cookies:
            if row['domain'] != self.origin.split('://',1)[1].split(':')[0]:
                continue
            self.jar.set_cookie(http.cookiejar.Cookie(0,row['name'],row['value'],None,False,row['domain'],False,False,'/',True,False,None,True,None,None,{}))
        self.opener=build_opener(HTTPCookieProcessor(self.jar))
        self.csrf=None
    def request(self,path,method='GET',body=None,expected=200):
        if method != 'GET' and self.csrf is None:
            self.csrf=self.request('/auth/csrf')['csrf_token']
        request=Request(self.origin+'/api/v1'+path,method=method,
                        data=json.dumps(body).encode() if body is not None else None,
                        headers={'Origin':self.origin,**({'Content-Type':'application/json','X-CSRF-Token':self.csrf} if method!='GET' else {})})
        try:
            response=self.opener.open(request,timeout=8)
        except HTTPError as error:
            response=error
        assert response.code==expected, f'Unexpected HTTP status for {path}: {response.code}'
        payload=json.loads(response.read())
        if 'csrf_token' in payload:
            self.csrf=payload['csrf_token']
        return payload


def inspect_instance(ops):
    return ops.runtime_resources()


def exec_python(ops,code,data=None):
    return ops.call('exec','-T','api','python','-c',code,
                    input=json.dumps(data).encode() if data is not None else None,capture_output=True).stdout


def await_health(ops):
    import urllib.request
    for _ in range(40):
        try:
            with urllib.request.urlopen(ops.spec['origin']+'/api/v1/health',timeout=1) as response:
                if response.status==200:
                    return
        except (OSError,HTTPError):
            pass
        time.sleep(1)
    raise RuntimeError('Synthetic instance health did not recover')


def login(ops):
    fixture=instances.read_env(ops.root/'private/synthetic.env')
    client=Client(ops.spec)
    client.request('/auth/csrf')
    client.request('/auth/login','POST',{'username':'synthetic','password':fixture['SYNTHETIC_PASSWORD']})
    # A restored factor retains its last-used step. Wait once, rather than weaken replay protection.
    time.sleep(31-int(time.time())%30)
    counter=struct.pack('>Q',int(time.time())//30)
    digest=hmac.new(base64.b32decode(fixture['SYNTHETIC_TOTP_SECRET']),counter,hashlib.sha1).digest()
    offset=digest[-1]&15
    code=str((struct.unpack('>I',digest[offset:offset+4])[0]&0x7fffffff)%1000000).zfill(6)
    client.request('/auth/totp/verify','POST',{'code':code})
    return client


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    args=parser.parse_args()
    root=args.root.resolve()
    domain=instances.PRODUCT['domain']
    peer_domain='work' if domain=='personal' else 'personal'
    primary=instances.InstanceOperations(root/domain)
    peer=instances.InstanceOperations(root/peer_domain)
    assert primary.spec['synthetic'] and peer.spec['synthetic'], 'Refuse real profiles'
    instances.preflight(primary.root,peer.root)
    cookies=[cookie for ops in (primary,peer) for cookie in json.loads((ops.root/'browser-state.json').read_text())['cookies']]
    p,w=Client(primary.spec,cookies),Client(peer.spec,cookies)
    p=login(primary)
    w=login(peer)
    backup_name=f'rehearsal-{time.time_ns()}.enc'
    evidence=[]
    def passed(message):
        evidence.append(message)
        print('PASS '+message,flush=True)
    p_resources,w_resources=inspect_instance(primary),inspect_instance(peer)
    assert p_resources['volume']!=w_resources['volume']
    passed('Separate running projects, networks, persistent volumes, API mounts and database ports verified')
    for own,foreign in ((primary,w_resources),(peer,p_resources)):
        socket.inet_aton(foreign['database_ip'])
        code="import socket,sys; target=sys.argv[1]\ntry:\n socket.create_connection((target,5432),timeout=2).close()\nexcept OSError:\n sys.exit(0)\nsys.exit(1)"
        own.call('exec','-T','api','python','-c',code,foreign['database_ip'],capture_output=True)
    passed('Docker network rejects database connections across instances in both directions')
    wrong_credentials="import json,sys,psycopg\nurl=json.load(sys.stdin)['url'].replace('postgresql+psycopg:','postgresql:')\ntry:\n psycopg.connect(url,connect_timeout=3).close()\nexcept psycopg.OperationalError:\n sys.exit(0)\nsys.exit(1)"
    for own,foreign in ((primary,peer),(peer,primary)):
        exec_python(own,wrong_credentials,{'url':instances.read_env(foreign.root/'private/api.env')['DATABASE_URL']})
    passed('Foreign credentials rejected by each independent PostgreSQL server')
    mismatch="import json,sys; from types import SimpleNamespace; from uuid import UUID; from app.database.session import engine; from app.core.instance import check_identity\ndata=json.load(sys.stdin)\nwith engine().connect() as connection:\n cursor=connection.connection.cursor()\n try:\n  check_identity(cursor,SimpleNamespace(instance_id=UUID(data['id']),instance_domain=data['domain']))\n except RuntimeError:\n  sys.exit(0)\nsys.exit(1)"
    for own,foreign in ((primary,peer),(peer,primary)):
        exec_python(own,mismatch,{'id':foreign.spec['instance_id'],'domain':foreign.spec['domain']})
    passed('Runtime identity guard rejects opposite domain even on a valid authenticated connection')
    restricted="""import sys
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from app.database.session import engine
with engine().connect() as c:
 name=c.engine.url.database
 for statement in [f"COMMENT ON DATABASE {name} IS 'forbidden'", 'CREATE TABLE forbidden_fixture(id int)', 'TRUNCATE tasks']:
  try:
   c.execute(text(statement))
  except DBAPIError:
   c.rollback()
   continue
  c.rollback()
  sys.exit(1)
"""
    for own in (primary,peer):
        exec_python(own,restricted)
    passed('Application roles cannot rebind identity, create schema objects or truncate tasks')
    assert p.request('/modules')['tasks'] and w.request('/modules')['tasks']
    personal_journal=p.request('/journal/entries')
    work_tasks=w.request('/tasks/export')
    work_entry=w.request('/journal/entries','POST',{'occurred_on':'2026-11-21','title':'Synthetic work boundary PH-CORE-003','content_markdown':'Synthetic work fixture only','projects':['Synthetic '+peer.spec['domain']+' project']},201)
    assert p.request('/journal/entries')==personal_journal
    assert w.request('/tasks/export')==work_tasks
    assert [i['name'] for i in p.request('/projects')]==['Synthetic '+primary.spec['domain']+' project']
    assert [i['name'] for i in w.request('/projects')]==['Synthetic '+peer.spec['domain']+' project']
    snapshot=p.request('/tasks/export')
    assert snapshot['tasks'] and all(t['title']!='Synthetic work boundary PH-CORE-003' for t in snapshot['tasks'])
    passed('Tasks enabled in both products; projects, journal and task data remain independent')
    primary.call('restart','db','api','web',capture_output=True)
    await_health(primary)
    assert p.request('/tasks/export')==snapshot
    assert w.request('/journal/entries/'+work_entry['id'])==work_entry
    passed('Task data and sessions survive database/API/web restart; peer fixture unchanged')
    token=p.request('/security/tokens','POST',{'name':'Synthetic restore revocation'})['token']
    primary.encrypted_backup(backup_name)
    assert b'Synthetic' not in (primary.root/'backups'/backup_name).read_bytes()
    passed('Per-instance encrypted backup created with identity manifest and separate key')
    # Cross-restore checks use synthetic fixture bytes, and must not invoke Docker at all.
    import shutil
    for name in (backup_name,backup_name+'.json'):
        shutil.copyfile(primary.root/'backups'/name,peer.root/'backups'/name)
    try:
        peer.encrypted_restore(backup_name,peer.spec['instance_id'],peer.spec['domain'])
    except ValueError:
        pass
    else:
        raise AssertionError('Cross-domain backup accepted')
    assert w.request('/journal/entries/'+work_entry['id'])==work_entry
    passed('Own backup rejected for peer instance before any restore; peer data unchanged')
    p.request('/tasks','POST',{'title':'Synthetic change after backup'},201)
    primary.encrypted_restore(backup_name,primary.spec['instance_id'],primary.spec['domain'])
    p.request('/tasks',expected=401)
    from urllib.request import urlopen
    try:
        urlopen(Request(primary.spec['origin']+'/api/v1/ingest/batches',data=b'{"import_key":"synthetic-revoked","candidates":[]}',headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'}),timeout=8)
    except HTTPError as error:
        assert error.code==401
    else:
        raise AssertionError('Restored collector token still active')
    fresh=login(primary)
    assert fresh.request('/tasks/export')==snapshot
    assert w.request('/journal/entries/'+work_entry['id'])==work_entry
    exec_python(primary,"from app.database.session import engine; from sqlalchemy import text\nwith engine().connect() as db:\n assert db.scalar(text('SELECT count(*) FROM collector_tokens WHERE revoked_at IS NULL'))==0")
    passed('Restore recovers original tasks, removes later synthetic change, revokes old session/token; fresh MFA login and peer instance valid')
    assert p_resources['volume']==inspect_instance(primary)['volume']
    (root/('operational-evidence-'+domain+'.json')).write_text(json.dumps({'result':'passed','checks':evidence,'real_operational_separation':'not_verified','production_ready':False},indent=2)+'\n')
    print('Operational isolation rehearsal PASS; no real installation accessed',flush=True)


if __name__=='__main__':
    try:
        main()
    except Exception:
        print('Operational rehearsal FAILED. Inspect private fixtures; no exception payload logged.',file=sys.stderr)
        raise SystemExit(1)
