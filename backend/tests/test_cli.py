import pytest
from sqlalchemy import select
from app.database.models import User
from app.cli import main
from app.security.service import password_hasher
from tests.helpers import PASSWORD


def test_interactive_initial_user(monkeypatch, db, capsys):
    monkeypatch.setattr('sys.argv', ['app.cli', 'create-user'])
    monkeypatch.setattr('builtins.input', lambda _: 'synthetic')
    monkeypatch.setattr('getpass.getpass', lambda _: PASSWORD)
    main()
    user = db.scalar(select(User))
    assert user.username == 'synthetic'
    assert password_hasher.verify(user.password_hash, PASSWORD)
    assert user.password_hash.startswith('$argon2id$')
    assert PASSWORD not in capsys.readouterr().out


def test_initial_user_rejects_password_mismatch(monkeypatch, db):
    monkeypatch.setattr('sys.argv', ['app.cli', 'create-user'])
    monkeypatch.setattr('builtins.input', lambda _: 'synthetic')
    values = iter([PASSWORD, 'different'])
    monkeypatch.setattr('getpass.getpass', lambda _: next(values))
    with pytest.raises(SystemExit):
        main()
    assert db.scalar(select(User)) is None


def test_initial_user_cannot_replace_existing(monkeypatch, user, db):
    monkeypatch.setattr('sys.argv', ['app.cli', 'create-user'])
    with pytest.raises(SystemExit, match='already exists'):
        main()
    assert db.scalar(select(User)).id == user.id
