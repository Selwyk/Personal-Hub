from alembic import context
from app.database.models import Base
from app.database.session import migration_engine

with migration_engine().connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()
