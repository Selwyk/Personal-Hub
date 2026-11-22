"""Run only in compose.ph-core-001-test.yaml's new disposable PostgreSQL."""
import json
import os
import uuid
from datetime import date

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from app.core.config import settings
from app.database.session import engine, session_factory
from app.database.models import Entry, Project, Tag, User


def snapshot():
    with engine().connect() as connection:
        return {name: sorted(json.dumps(row, sort_keys=True, default=str)
                             for row in connection.scalars(text(f'SELECT to_jsonb(t) FROM "{name}" AS t')))
                for name in inspect(connection).get_table_names() if name != "alembic_version"}


def main():
    current = engine()
    if current.url.database != "hub_test" or current.url.host != "db":
        raise RuntimeError("Refuse migration experiment outside disposable test compose")
    database = f"ph_core001_migration_{uuid.uuid4().hex}_test"
    with current.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text(f'CREATE DATABASE "{database}"'))
    os.environ["DATABASE_URL"] = current.url.set(database=database).render_as_string(hide_password=False)
    settings.cache_clear()
    engine.cache_clear()
    config = Config("alembic.ini")
    command.upgrade(config, "a172001")
    with session_factory()() as db:
        db.add(User(username="synthetic-preserved", password_hash="synthetic-hash-only"))
        db.add(Entry(occurred_on=date(2026, 11, 21), title="Synthetic preserved",
                     content_markdown="Synthetic content", projects=[Project(name="Synthetic", normalized_name="synthetic")],
                     tags=[Tag(name="Fixture", normalized_name="fixture")]))
        db.commit()
    before = snapshot()
    command.upgrade(config, "head")
    after = snapshot()
    assert {name: after[name] for name in before} == before, "Existing rows changed"
    assert set(after) - set(before) == {"tasks", "task_projects", "task_tags"}
    command.check(config)
    print("Migration preservation: all pre-existing synthetic rows unchanged; 3 additive tables; alembic check passed")


if __name__ == "__main__":
    main()
