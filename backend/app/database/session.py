from functools import lru_cache

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.instance import check_identity


def configured_engine(url, purpose="app"):
    config = settings()
    result = create_engine(url, pool_pre_ping=True)
    if config.isolation_required:
        @event.listens_for(result, "connect")
        def verify(connection, record):
            try:
                with connection.cursor() as cursor:
                    check_identity(cursor, config, purpose)
            finally:
                connection.rollback()
    return result


@lru_cache
def engine():
    return configured_engine(settings().database_url)


def migration_engine():
    config = settings()
    if not config.isolation_required:
        return engine()
    if not config.migration_database_url:
        raise RuntimeError("Isolated migrations require separate migrator credentials")
    return configured_engine(config.migration_database_url.get_secret_value(), "migrate")


def session_factory():
    return sessionmaker(engine(), expire_on_commit=False)


def get_db():
    with session_factory()() as db:
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
