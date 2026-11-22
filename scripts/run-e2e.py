"""Run inherited E2E scenarios on new isolated, disposable databases only.

Requires own test/web images and npm dependencies. Stops only its fresh UUID project.
No production configuration, .env or sibling checkout is read.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from urllib.request import urlopen
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['tasks', 'journal', 'bootstrap'], required=True)
    parser.add_argument('--port', type=int, required=True)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('Use an unoccupied local test port')
    product = json.loads((ROOT/'backend/product.json').read_text(encoding='utf-8'))
    run_id = product['slug'] + '-e2e-' + uuid4().hex
    target = ROOT/'.runtime'/run_id
    target.mkdir(parents=True, exist_ok=False)
    origin = f'http://localhost:{args.port}'
    environment = {
        'DATABASE_URL': 'postgresql+psycopg://fixture:fixture-only@db:5432/hub_e2e',
        'TOTP_ENCRYPTION_KEY': 'MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=',
        'APP_PUBLIC_URL': origin, 'WEBAUTHN_RP_ID': 'localhost', 'WEBAUTHN_ORIGINS': origin,
        'COOKIE_SECURE': 'false', 'TASKS_ENABLED': 'true', 'APP_TIMEZONE': 'Europe/Madrid',
    }
    health = {'interval':'2s','timeout':'3s','retries':30}
    if args.mode=='bootstrap':
        environment['BOOTSTRAP_PASSWORD']='Synthetic temporary credential 2026!'
    seed = 'alembic upgrade head' if args.mode=='bootstrap' else 'python -m tests.e2e_seed'
    compose = {'name':run_id,'services':{
        'db':{'image':json.loads((ROOT/'compose.test.yaml').read_text())['services']['db']['image'],
              'environment':{'POSTGRES_USER':'fixture','POSTGRES_PASSWORD':'fixture-only','POSTGRES_DB':'hub_e2e'},
              'tmpfs':['/var/lib/postgresql'],
              'healthcheck':{**health,'test':['CMD-SHELL','pg_isready -U fixture -d hub_e2e']}},
        'api':{'image':product['slug']+'-test:local','environment':environment,
               'command':['sh','-c',seed+' && exec uvicorn app.main:app --host 0.0.0.0 --no-access-log'],
               'depends_on':{'db':{'condition':'service_healthy'}},
               'healthcheck':{**health,'test':['CMD','python','-c',"import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health')"]}},
        'web':{'image':product['slug']+'-web:local','ports':[f'127.0.0.1:{args.port}:8080'],
               'depends_on':{'api':{'condition':'service_healthy'}}},
    }}
    config = target/'compose.json'
    config.write_text(json.dumps(compose,indent=2)+'\n',encoding='utf-8')
    env = {**os.environ,'COMPOSE_DISABLE_ENV_FILE':'1','HUB_E2E_URL':origin,
           'HUB_TASKS_E2E':'0','HUB_JOURNAL_E2E':'0','HUB_BOOTSTRAP_E2E':'0'}
    env['HUB_'+args.mode.upper()+'_E2E']='1'
    command = ['docker','compose','--project-name',run_id,'-f',str(config)]
    result = 1
    try:
        subprocess.run(command+['up','-d','--wait','web'],env=env,check=True)
        for _ in range(40):
            try:
                with urlopen(origin+'/api/v1/health',timeout=1) as response:
                    if response.status==200:
                        break
            except OSError:
                time.sleep(1)
        scenario = {'journal':'hub','tasks':'tasks','bootstrap':'bootstrap'}[args.mode]
        npm = shutil.which('npx.cmd') if os.name=='nt' else shutil.which('npx')
        if not npm:
            raise RuntimeError('Install Node/npm in the test environment')
        with (target/'playwright.log').open('w',encoding='utf-8') as output:
            completed = subprocess.run([npm,'playwright','test',f'e2e/{scenario}.spec.ts'],cwd=ROOT/'frontend',env=env,stdout=output,stderr=subprocess.STDOUT)
        print((target/'playwright.log').read_text(encoding='utf-8'),flush=True)
        result = completed.returncode
        (target/'result.json').write_text(json.dumps({'mode':args.mode,'result':'passed' if result==0 else 'failed','project':run_id})+'\n')
    finally:
        subprocess.run(command+['stop'],env=env,check=True)
    return result

if __name__=='__main__':
    raise SystemExit(main())
