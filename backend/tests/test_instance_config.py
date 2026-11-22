import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.product import DOMAIN
from app.core.instance import check_identity, database_name, role_name


def isolated(**changes):
    identity = uuid4()
    values = dict(instance_id=identity, instance_domain=DOMAIN, isolation_required=True,
                  database_url=f'postgresql+psycopg://{role_name(identity,"app")}:synthetic-only@db/{database_name(identity,DOMAIN)}')
    return Settings(**{**values, **changes})


def test_legacy_cookie_names_and_opt_in_identity():
    assert Settings().session_cookie == 'hub_session'
    assert Settings().device_cookie == 'hub_device'
    one, two = isolated(), isolated()
    assert one.session_cookie != two.session_cookie and one.device_cookie != two.device_cookie
    assert one.instance_id.hex in one.session_cookie
    secure = isolated(app_public_url='https://personal.example.invalid', webauthn_rp_id='personal.example.invalid',
                      webauthn_origins='https://personal.example.invalid', cookie_secure=True)
    assert secure.session_cookie.startswith('__Host-')


@pytest.mark.parametrize('changes', [
    {'instance_id': None}, {'instance_domain': None}, {'instance_domain': 'mixed'},
    {'isolation_required': False}, {'database_url': 'postgresql+psycopg://wrong:fixture@db/hub'},
    {'webauthn_origins': 'http://localhost:5173,http://localhost:5173'},
    {'migration_database_url': 'postgresql+psycopg://admin:fixture@db/hub'},
])
def test_invalid_isolation_settings_rejected(changes):
    with pytest.raises(ValidationError):
        isolated(**changes)


class Cursor:
    def __init__(self, row):
        self.row = row
    def execute(self, statement):
        assert 'shobj_description' in statement
    def fetchone(self):
        return self.row


def identity_row(config, purpose='app'):
    marker = json.dumps({'personalhub': 1, 'instance_id': str(config.instance_id), 'domain': config.instance_domain})
    return [database_name(config.instance_id, config.instance_domain), role_name(config.instance_id, purpose), marker,
            False, False, purpose == 'migrate']


def test_identity_check_allows_expected_app_and_separate_migrator():
    config = SimpleNamespace(instance_id=uuid4(), instance_domain='personal')
    check_identity(Cursor(identity_row(config)), config)
    check_identity(Cursor(identity_row(config, 'migrate')), config, 'migrate')


@pytest.mark.parametrize('column,value', [(0,'other_database'),(1,'other_role'),(2,None),(2,'invalid-json'),
                                         (2,'{}'),(3,True),(4,True),(5,True)])
def test_identity_or_privilege_mismatch_fails_without_private_details(column, value):
    config = SimpleNamespace(instance_id=uuid4(), instance_domain='personal')
    row = identity_row(config)
    row[column] = value
    with pytest.raises(RuntimeError, match='Database instance identity or role privileges'):
        check_identity(Cursor(row), config)


def test_no_database_identity_is_repaired_implicitly():
    config = SimpleNamespace(instance_id=uuid4(), instance_domain='work')
    with pytest.raises(RuntimeError):
        check_identity(Cursor(None), config)
