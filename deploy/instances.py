"""Prepare independent instances; never starts or migrates an installation.

Generated files are private. CLI diagnostics intentionally omit exception values.
"""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
from urllib.parse import urlparse
from uuid import UUID, uuid4

try:
    from .operations import Operations
except ImportError:
    from operations import Operations

_product_path = Path(__file__).resolve().parents[1] / "backend/product.json"
if not _product_path.exists():
    _product_path = Path.cwd() / "product.json"
PRODUCT = json.loads(_product_path.read_text(encoding="utf-8"))

POSTGRES = "postgres:18.6@sha256:5a5a84b19854a9ffaa54082c166ff4ec27473a361e496e5ea167f298f2da9722"
FILES = ("instance.json", "compose.json", "private/api.env", "private/migrate.env",
         "private/db.env", "private/init.sql", "private/backup.key", "nginx.conf")


def names(spec):
    token = UUID(spec["instance_id"]).hex
    return {"database": f"hub_{spec['domain']}_{token}",
            **{role: f"hub_{role}_{token}" for role in ("app", "migrate", "admin")}}


def env_text(values):
    if any("\n" in str(v) or "\r" in str(v) for v in values.values()):
        raise ValueError("Invalid environment value")
    return "".join(f"{k}={v}\n" for k, v in sorted(values.items()))


def read_env(path):
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, value = line.split("=", 1)
        if key in values or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ValueError("Invalid environment file")
        values[key] = value
    return values


def validate_spec(spec, root):
    identity = UUID(spec["instance_id"])
    origin = urlparse(spec["origin"])
    if (spec["format"] != 1 or spec["domain"] not in ("personal", "work")
            or spec["root"] != str(root.resolve())
            or spec["project"] != f"ph-{spec['domain']}-{identity.hex}"
            or type(spec["synthetic"]) is not bool or type(spec["port"]) is not int
            or not 1024 <= spec["port"] <= 65535
            or origin.path or origin.params or origin.query or origin.fragment
            or origin.username or origin.password or not origin.hostname
            or origin.scheme not in ("https", "http")
            or (origin.scheme == "http" and (not spec["synthetic"] or origin.hostname not in ("localhost", "127.0.0.1")))):
        raise ValueError("Invalid instance specification")
    proxy = spec.get("proxy_network")
    if proxy is not None and (not isinstance(proxy, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,62}", proxy)
            or proxy in {"host", "none", "bridge"}
            or proxy.startswith(spec["project"] + "_")):
        raise ValueError("Use an existing dedicated proxy network")
    if not spec["synthetic"] and any(not re.fullmatch(
            r"(?:sha256:[0-9a-f]{64}|[^\s]+@sha256:[0-9a-f]{64})", spec["images"][key])
            for key in ("api", "web")):
        raise ValueError("Real profiles require pinned image digests or local content IDs")
    return origin


def compose_for(spec, root):
    def file(name):
        return (root / name).resolve().as_posix()
    api_env, migrate_env = file("private/api.env"), file("private/migrate.env")
    api_image = spec["images"]["api"]
    health = {"interval": "2s", "timeout": "3s", "retries": 30}
    api_service = {
        "image": api_image, "env_file": [api_env], "networks": ["edge", "data"],
        "command": ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers", "--no-access-log"],
        "depends_on": {"migrate": {"condition": "service_completed_successfully"}},
        "security_opt": ["no-new-privileges:true"], "cap_drop": ["ALL"],
        "healthcheck": {**health, "test": ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health')"]},
    }
    services = {
        "db": {"image": POSTGRES, "env_file": [file("private/db.env")], "networks": ["data"],
            "command": ["postgres", "-c", "log_statement=none", "-c", "log_min_error_statement=panic", "-c", "log_parameter_max_length_on_error=0", "-c", "log_error_verbosity=terse"],
            "volumes": [{"type": "volume", "source": "database", "target": "/var/lib/postgresql"},
                        {"type": "bind", "source": file("private/init.sql"), "target": "/docker-entrypoint-initdb.d/10-instance.sql", "read_only": True}],
            "healthcheck": {**health, "test": ["CMD-SHELL", 'pg_isready -U "$${POSTGRES_USER}" -d "$${POSTGRES_DB}"']}},
        "migrate": {"image": api_image, "env_file": [migrate_env], "networks": ["data"],
            "command": ["alembic", "upgrade", "head"], "depends_on": {"db": {"condition": "service_healthy"}}},
        "api": api_service,
        "web": {"image": spec["images"]["web"], "networks": ["edge"],
            "ports": [f"127.0.0.1:{spec['port']}:8080"],
            "volumes": [{"type": "bind", "source": file("nginx.conf"), "target": "/etc/nginx/conf.d/default.conf", "read_only": True}],
            "depends_on": {"api": {"condition": "service_healthy"}},
            "security_opt": ["no-new-privileges:true"], "cap_drop": ["ALL"],
            "healthcheck": {**health, "test": ["CMD", "wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8080/healthz"]}},
    }
    if spec["synthetic"]:
        services["seed"] = {"image": api_image, "env_file": [api_env, file("private/synthetic.env")],
                            "networks": ["data"], "command": ["python", "-m", "tests.instance_seed"], "profiles": ["fixture"]}
    networks = {"edge": {}, "data": {"internal": True}}
    if spec.get("proxy_network"):
        # Only the web joins the existing proxy network. No host port is opened.
        services["web"].pop("ports")
        services["web"]["networks"] = {
            "edge": {}, "proxy": {"aliases": ["personal-hub-" + UUID(spec["instance_id"]).hex]}}
        networks["proxy"] = {"external": True, "name": spec["proxy_network"]}
        for service in ("db", "api", "web"):
            services[service]["restart"] = "unless-stopped"
            services[service]["logging"] = {"driver": "json-file", "options": {"max-size": "10m", "max-file": "3"}}
    return {"name": spec["project"], "services": services,
            "networks": networks, "volumes": {"database": {}}}


def init_sql(spec, app_password, migrate_password):
    n = names(spec)
    # Identifiers derive exclusively from UUID/domain; passwords are URL-safe generated values.
    marker = json.dumps({"personalhub": 1, "instance_id": spec["instance_id"], "domain": spec["domain"]})
    return f"""\\set ON_ERROR_STOP on
CREATE ROLE {n['app']} LOGIN PASSWORD '{app_password}' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
CREATE ROLE {n['migrate']} LOGIN PASSWORD '{migrate_password}' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
REVOKE ALL ON DATABASE {n['database']} FROM PUBLIC;
GRANT CONNECT ON DATABASE {n['database']} TO {n['app']}, {n['migrate']};
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO {n['app']};
GRANT USAGE, CREATE ON SCHEMA public TO {n['migrate']};
CREATE EXTENSION IF NOT EXISTS pg_trgm;
ALTER DEFAULT PRIVILEGES FOR ROLE {n['migrate']} IN SCHEMA public GRANT SELECT,INSERT,UPDATE,DELETE ON TABLES TO {n['app']};
ALTER DEFAULT PRIVILEGES FOR ROLE {n['migrate']} IN SCHEMA public GRANT USAGE,SELECT ON SEQUENCES TO {n['app']};
COMMENT ON DATABASE {n['database']} IS '{marker}';
"""


def nginx_for(spec):
    origin = urlparse(spec["origin"])
    if not re.fullmatch(r"[A-Za-z0-9.-]+", origin.hostname):
        raise ValueError("Unsupported origin hostname")
    return ("server {\n  listen 8080;\n  server_name " + origin.hostname + ";\n  set $hub_scheme "
            + origin.scheme + ";\n  include /etc/nginx/snippets/hub-common.conf;\n}\n")


def same_bind_source(actual, expected):
    expected = expected.resolve().as_posix()
    allowed = [expected]
    if os.name == 'nt' and re.match(r'^[A-Za-z]:/', expected):
        # Docker Desktop exposes both forms, depending on the bind endpoint.
        allowed.append('/run/desktop/mnt/host/'+expected[0].lower()+'/'+expected[3:])
        return actual.casefold() in {p.casefold() for p in allowed}
    return actual in allowed


def generate(root, domain, origin, port, *, synthetic=False, api_image=None, web_image=None, proxy_network=None):
    root = root.resolve()
    spec = dict(format=1, instance_id=str(uuid4()), domain=domain, root=str(root), origin=origin.rstrip("/"),
                port=port, synthetic=synthetic,
                images={"api": api_image or (PRODUCT["slug"] + "-api:local"), "web": web_image or (PRODUCT["slug"] + "-web:local")})
    spec["project"] = f"ph-{domain}-{UUID(spec['instance_id']).hex}"
    if proxy_network is not None:
        spec["proxy_network"] = proxy_network
    parsed = validate_spec(spec, root)
    if root.exists():
        raise ValueError("Refuse to overwrite an instance directory")
    n = names(spec)
    passwords = {key: secrets.token_urlsafe(36) for key in ("app", "migrate", "admin")}
    api = {"DATABASE_URL": f"postgresql+psycopg://{n['app']}:{passwords['app']}@db:5432/{n['database']}",
           "TOTP_ENCRYPTION_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
           "ISOLATION_REQUIRED": "true", "INSTANCE_ID": spec["instance_id"], "INSTANCE_DOMAIN": domain,
           "APP_PUBLIC_URL": spec["origin"], "WEBAUTHN_RP_ID": parsed.hostname, "WEBAUTHN_ORIGINS": spec["origin"],
           "COOKIE_SECURE": "true" if parsed.scheme == "https" else "false", "TASKS_ENABLED": "true",
           "APP_TIMEZONE": "Europe/Madrid"}
    migrate = {**api, "MIGRATION_DATABASE_URL": f"postgresql+psycopg://{n['migrate']}:{passwords['migrate']}@db:5432/{n['database']}"}
    db = {"POSTGRES_DB": n["database"], "POSTGRES_USER": n["admin"], "POSTGRES_PASSWORD": passwords["admin"]}
    contents = {"instance.json": json.dumps(spec, indent=2) + "\n", "compose.json": json.dumps(compose_for(spec, root), indent=2) + "\n",
                "private/api.env": env_text(api), "private/migrate.env": env_text(migrate), "private/db.env": env_text(db),
                "private/init.sql": init_sql(spec, passwords['app'], passwords['migrate']),
                "private/backup.key": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode() + "\n", "nginx.conf": nginx_for(spec)}
    if synthetic:
        contents["private/synthetic.env"] = env_text({"HUB_SYNTHETIC_INSTANCE": "1",
            "SYNTHETIC_PASSWORD": secrets.token_urlsafe(32), "SYNTHETIC_TOTP_SECRET": base64.b32encode(secrets.token_bytes(20)).decode()})
    root.mkdir(parents=True, mode=0o700)
    for directory in ("private", "backups", "auxiliary", "auxiliary/collector", "auxiliary/collector/outbox", "auxiliary/collector/checkpoints", "auxiliary/logs"):
        (root / directory).mkdir(parents=True, mode=0o700)
    for name, content in contents.items():
        path = root / name
        with path.open("x", encoding="utf-8", newline="\n") as target:
            target.write(content)
        path.chmod(0o600)
    preflight(root)
    return spec


def preflight(root, peer=None):
    root = root.resolve()
    spec = json.loads((root / "instance.json").read_text(encoding="utf-8"))
    origin = validate_spec(spec, root)
    for name in FILES + (("private/synthetic.env",) if spec["synthetic"] else ()):
        p = root / name
        if p.is_symlink() or not p.is_file() or not p.resolve().is_relative_to(root):
            raise ValueError("Unsafe instance path")
        if os.name == "posix" and p.stat().st_mode & 0o077:
            raise ValueError("Private instance files require owner-only access")
    for name in ("private", "backups", "auxiliary", "auxiliary/collector/outbox", "auxiliary/collector/checkpoints", "auxiliary/logs"):
        p = root / name
        if p.is_symlink() or not p.is_dir() or not p.resolve().is_relative_to(root):
            raise ValueError("Unsafe instance directory")
        if os.name == "posix" and p.stat().st_mode & 0o077:
            raise ValueError("Private instance directories require owner-only access")
    if not spec["synthetic"] and os.name != "posix":
        raise ValueError("Real activation requires verified private filesystem permissions on its target host")
    if json.loads((root / "compose.json").read_text()) != compose_for(spec, root):
        raise ValueError("Compose differs from isolated template")
    api, migrate, db = [read_env(root / "private" / f"{name}.env") for name in ("api", "migrate", "db")]
    n = names(spec)
    urls = [(urlparse(api["DATABASE_URL"]), "app"), (urlparse(migrate["MIGRATION_DATABASE_URL"]), "migrate")]
    for url, purpose in urls:
        if (url.scheme != "postgresql+psycopg" or url.hostname != "db" or url.port != 5432
                or url.username != n[purpose] or url.path != "/" + n["database"] or url.query or url.fragment
                or not url.password or not re.fullmatch(r"[A-Za-z0-9_-]{32,}", url.password)):
            raise ValueError("Invalid isolated connection")
    app_password, migration_password = [url.password for url, _ in urls]
    expected_keys = {"DATABASE_URL", "TOTP_ENCRYPTION_KEY", "ISOLATION_REQUIRED", "INSTANCE_ID", "INSTANCE_DOMAIN",
                     "APP_PUBLIC_URL", "WEBAUTHN_RP_ID", "WEBAUTHN_ORIGINS", "COOKIE_SECURE", "TASKS_ENABLED", "APP_TIMEZONE"}
    if (set(api) != expected_keys or migrate != {**api, "MIGRATION_DATABASE_URL": migrate["MIGRATION_DATABASE_URL"]}
            or db != {"POSTGRES_DB": n["database"], "POSTGRES_USER": n["admin"], "POSTGRES_PASSWORD": db["POSTGRES_PASSWORD"]}
            or api["ISOLATION_REQUIRED"] != "true" or api["INSTANCE_ID"] != spec["instance_id"]
            or api["INSTANCE_DOMAIN"] != spec["domain"] or api["APP_PUBLIC_URL"] != spec["origin"]
            or api["WEBAUTHN_ORIGINS"] != spec["origin"] or api["WEBAUTHN_RP_ID"] != origin.hostname
            or api["COOKIE_SECURE"] != ("true" if origin.scheme == "https" else "false")
            or api["TASKS_ENABLED"] != "true"
            or api["APP_TIMEZONE"] != "Europe/Madrid"):
        raise ValueError("Instance environment does not match manifest")
    key = (root / "private/backup.key").read_text().strip()
    if any(len(base64.urlsafe_b64decode(value)) != 32 for value in (key, api["TOTP_ENCRYPTION_KEY"])):
        raise ValueError("Invalid encryption key")
    if (not re.fullmatch(r"[A-Za-z0-9_-]{32,}", db['POSTGRES_PASSWORD'])
            or len({app_password, migration_password, db['POSTGRES_PASSWORD'], key, api['TOTP_ENCRYPTION_KEY']}) != 5):
        raise ValueError("Instance secrets must be distinct")
    if (root / "private/init.sql").read_text() != init_sql(spec, app_password, migration_password):
        raise ValueError("Database initializer does not match identity")
    if (root / "nginx.conf").read_text() != nginx_for(spec):
        raise ValueError("Ingress origin does not match manifest")
    if peer is not None:
        other_root = peer.resolve()
        other = preflight(other_root)
        if (root.is_relative_to(other_root) or other_root.is_relative_to(root)
                or spec["instance_id"] == other["instance_id"] or spec["domain"] == other["domain"]
                or spec["port"] == other["port"] or origin.hostname == urlparse(other["origin"]).hostname):
            raise ValueError("Instances overlap")
        other_api = read_env(other_root / "private/api.env")
        other_db = read_env(other_root / "private/db.env")
        other_migrate = read_env(other_root / "private/migrate.env")
        own = {app_password, migration_password, db['POSTGRES_PASSWORD'], key, api['TOTP_ENCRYPTION_KEY']}
        theirs = {urlparse(other_api['DATABASE_URL']).password, urlparse(other_migrate['MIGRATION_DATABASE_URL']).password,
                  other_db['POSTGRES_PASSWORD'], other_api['TOTP_ENCRYPTION_KEY'], (other_root/'private/backup.key').read_text().strip()}
        if own & theirs:
            raise ValueError("Instances share secrets")
    return spec


class InstanceOperations(Operations):
    def __init__(self, root, run=subprocess.run):
        self.root = root.resolve()
        self.spec = preflight(self.root)
        super().__init__([str(self.root / "compose.json")], run, migration_service="migrate")
        self.compose[2:2] = ["--project-name", self.spec["project"]]

    def call(self, *args, **kwargs):
        env = dict(os.environ)
        for key in ("COMPOSE_FILE", "COMPOSE_PROJECT_NAME", "COMPOSE_PROFILES", "COMPOSE_ENV_FILES"):
            env.pop(key, None)
        env["COMPOSE_DISABLE_ENV_FILE"] = "1"
        kwargs.setdefault("env", env)
        if not kwargs.get("capture_output"):
            kwargs.setdefault("stderr", subprocess.PIPE)
        return super().call(*args, **kwargs)

    def verify(self):
        self.call("run", "--rm", "--no-deps", "api", "python", "-c",
                  "from app.database.session import engine; engine().connect().close()", stdout=subprocess.PIPE)

    def runtime_resources(self):
        """Read-only comparison of desired isolation with the running containers."""
        project = self.spec['project']
        result = {}
        for service, networks in (('db',['data']),('api',['edge','data']),('web',['edge'])):
            container = self.call('ps','-q',service,capture_output=True).stdout.decode().strip()
            if not container:
                raise ValueError('Prepared container is not running')
            row = json.loads(self.run(['docker','inspect',container],check=True,capture_output=True).stdout)[0]
            expected_image = POSTGRES if service=='db' else self.spec['images']['web' if service=='web' else 'api']
            image = json.loads(self.run(['docker','image','inspect',expected_image],check=True,capture_output=True).stdout)[0]
            expected_networks = {project+'_'+n for n in networks}
            if service == 'web' and self.spec.get('proxy_network'):
                expected_networks.add(self.spec['proxy_network'])
            if (row['Config']['Labels']['com.docker.compose.project'] != project
                    or set(row['NetworkSettings']['Networks']) != expected_networks
                    or row['Config']['Image'] != expected_image or row['Image'] != image['Id']):
                raise ValueError('Running project, image or attached networks differ')
            if self.spec.get('proxy_network'):
                if any(row['NetworkSettings']['Ports'].values()):
                    raise ValueError('Proxy profiles must not publish application host ports')
                if (row['HostConfig']['RestartPolicy']['Name'] != 'unless-stopped'
                        or row['HostConfig']['LogConfig'] != {
                            'Type': 'json-file', 'Config': {'max-size': '10m', 'max-file': '3'}}):
                    raise ValueError('Runtime restart or log rotation policy differs')
            if service == 'web' and self.spec.get('proxy_network'):
                network = row['NetworkSettings']['Networks'][self.spec['proxy_network']]
                if 'personal-hub-' + UUID(self.spec['instance_id']).hex not in network.get('Aliases', []):
                    raise ValueError('Proxy ingress alias differs')
            actual_env = dict(v.split('=',1) for v in row['Config']['Env'])
            if service in ('db','api'):
                expected_env = read_env(self.root/'private'/f'{service}.env')
                if any(actual_env.get(key) != value for key,value in expected_env.items()):
                    raise ValueError('Running environment differs from private configuration')
            if service == 'db':
                volumes = [m['Name'] for m in row['Mounts'] if m['Type']=='volume']
                binds = [m for m in row['Mounts'] if m['Type']=='bind']
                if (volumes != [project+'_database'] or len(row['Mounts']) != 2
                        or len(binds) != 1 or binds[0]['Destination'] != '/docker-entrypoint-initdb.d/10-instance.sql'
                        or binds[0]['RW'] or not same_bind_source(binds[0]['Source'], self.root/'private/init.sql')
                        or any(v for v in row['NetworkSettings']['Ports'].values())):
                    raise ValueError('Database volume, mounts or published ports differ')
                result['volume'] = volumes[0]
                result['database_ip'] = row['NetworkSettings']['Networks'][project+'_data']['IPAddress']
            if service == 'api' and (row['Mounts'] or set(actual_env) & {'POSTGRES_PASSWORD','MIGRATION_DATABASE_URL','AZURE_DEVOPS_PAT'}):
                raise ValueError('API has unexpected mounts or privileged credentials')
            if service == 'web' and (len(row['Mounts']) != 1 or row['Mounts'][0]['Type'] != 'bind'
                    or row['Mounts'][0]['Destination'] != '/etc/nginx/conf.d/default.conf'
                    or row['Mounts'][0]['RW'] or not same_bind_source(row['Mounts'][0]['Source'],self.root/'nginx.conf')):
                raise ValueError('Ingress mounts differ')
            result[service] = container
        data = json.loads(self.run(['docker','network','inspect',project+'_data'],check=True,capture_output=True).stdout)[0]
        if not data['Internal']:
            raise ValueError('Data network is not internal')
        return result

    def backup_path(self, name):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}\.enc", name):
            raise ValueError("Use an encrypted backup filename within this instance")
        result = self.root / "backups" / name
        if result.is_symlink():
            raise ValueError("Refuse backup symlink")
        return result

    def encrypted_backup(self, name):
        from cryptography.fernet import Fernet
        self.verify()
        destination = self.backup_path(name)
        if destination.exists() or destination.with_suffix('.enc.json').exists():
            raise ValueError("Refuse overwrite")
        with tempfile.TemporaryDirectory(prefix="backup-", dir=self.root / "private") as temporary:
            raw = Path(temporary) / "backup.dump"
            super().backup(raw)
            if raw.stat().st_size > 512 * 1024 * 1024:
                raise ValueError("Backup exceeds supported in-memory encryption size")
            encrypted = Fernet((self.root/"private/backup.key").read_bytes().strip()).encrypt(raw.read_bytes())
        with destination.open('xb') as output:
            output.write(encrypted)
        destination.chmod(0o600)
        manifest = {"format": "personalhub-encrypted-v1", "instance_id": self.spec['instance_id'],
                    "domain": self.spec['domain'], "database": names(self.spec)['database'],
                    "sha256": hashlib.sha256(encrypted).hexdigest(), "created_at": datetime.now(timezone.utc).isoformat()}
        with destination.with_suffix('.enc.json').open('x', encoding='utf-8') as output:
            json.dump(manifest, output, indent=2)
        destination.with_suffix('.enc.json').chmod(0o600)

    def encrypted_restore(self, name, confirm_id, confirm_domain):
        from cryptography.fernet import Fernet
        if confirm_id != self.spec['instance_id'] or confirm_domain != self.spec['domain']:
            raise ValueError("Instance confirmation mismatch")
        source = self.backup_path(name)
        if source.with_suffix('.enc.json').stat().st_size > 16384:
            raise ValueError("Invalid backup manifest size")
        manifest = json.loads(source.with_suffix('.enc.json').read_text())
        if (manifest['format'] != 'personalhub-encrypted-v1' or manifest['instance_id'] != confirm_id
                or manifest['domain'] != confirm_domain or manifest['database'] != names(self.spec)['database']
                or source.stat().st_size > 700 * 1024 * 1024):
            raise ValueError("Backup identity mismatch")
        encrypted = source.read_bytes()
        if hashlib.sha256(encrypted).hexdigest() != manifest['sha256']:
            raise ValueError("Backup integrity mismatch")
        raw = Fernet((self.root/'private/backup.key').read_bytes().strip()).decrypt(encrypted)
        if len(raw) > 512 * 1024 * 1024:
            raise ValueError("Backup exceeds supported size")
        self.verify()
        with tempfile.TemporaryDirectory(prefix='restore-', dir=self.root/'private') as temporary:
            dump = Path(temporary)/'restore.dump'
            dump.write_bytes(raw)
            dump.with_suffix('.dump.json').write_text(json.dumps({'format': 1, 'database': names(self.spec)['database'], 'sha256': hashlib.sha256(raw).hexdigest()}))
            try:
                super().restore(dump, names(self.spec)['database'])
                self.verify()
            except Exception:
                # Includes a failed final identity check after health resumed ingress.
                self.call('stop', 'web', 'api')
                raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    create = sub.add_parser('prepare')
    create.add_argument('--root', type=Path, required=True)
    create.add_argument('--domain', choices=[PRODUCT['domain']], required=True)
    create.add_argument('--origin', required=True)
    create.add_argument('--port', type=int, required=True)
    create.add_argument('--synthetic', action='store_true')
    create.add_argument('--api-image')
    create.add_argument('--web-image')
    create.add_argument('--proxy-network', help='Existing proxy network; web only, no published host ports')
    for action in ('preflight','verify','backup','restore'):
        command = sub.add_parser(action)
        command.add_argument('--root', type=Path, required=True)
        if action == 'preflight':
            command.add_argument('--against', type=Path)
        if action in ('backup','restore'):
            command.add_argument('--file', required=True)
        if action == 'restore':
            command.add_argument('--confirm-instance', required=True)
            command.add_argument('--confirm-domain', choices=['personal','work'], required=True)
    args = parser.parse_args()
    try:
        if args.action == 'prepare':
            generate(args.root,args.domain,args.origin,args.port,synthetic=args.synthetic,
                     api_image=args.api_image,web_image=args.web_image,proxy_network=args.proxy_network)
        elif args.action == 'preflight':
            own = preflight(args.root,args.against)
            if own['domain'] != PRODUCT['domain']:
                raise ValueError('Profile belongs to the other product')
        else:
            ops = InstanceOperations(args.root)
            if ops.spec['domain'] != PRODUCT['domain']:
                raise ValueError('Profile belongs to the other product')
            if args.action == 'verify':
                ops.runtime_resources()
                ops.verify()
            elif args.action == 'backup':
                ops.encrypted_backup(args.file)
            else:
                ops.encrypted_restore(args.file,args.confirm_instance,args.confirm_domain)
    except Exception:
        print('Instance operation failed. Check private configuration; keep ingress stopped if restoration began.', file=sys.stderr)
        return 1
    print(f'Instance {args.action}: PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
