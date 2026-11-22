"""Opt-in database identity checks. Public identity only; never repair on connect."""
import json


def database_name(instance_id, domain):
    return f"hub_{domain}_{instance_id.hex}"


def role_name(instance_id, purpose):
    return f"hub_{purpose}_{instance_id.hex}"


def check_identity(cursor, config, purpose="app"):
    cursor.execute("""SELECT current_database(), current_user,
        shobj_description(d.oid, 'pg_database'),
        r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication OR r.rolbypassrls,
        pg_has_role(current_user, d.datdba, 'MEMBER'),
        has_schema_privilege(current_user, 'public', 'CREATE')
        FROM pg_database d JOIN pg_roles r ON r.rolname = current_user
        WHERE d.datname = current_database()"""
    )
    row = cursor.fetchone()
    try:
        marker = json.loads(row[2]) if row else None
    except (ValueError, TypeError):
        marker = None
    expected = {"personalhub": 1, "instance_id": str(config.instance_id), "domain": config.instance_domain}
    if (not row or row[0] != database_name(config.instance_id, config.instance_domain)
            or row[1] != role_name(config.instance_id, purpose) or marker != expected
            or row[3] or row[4] or bool(row[5]) != (purpose == "migrate")):
        raise RuntimeError("Database instance identity or role privileges do not match configuration")
