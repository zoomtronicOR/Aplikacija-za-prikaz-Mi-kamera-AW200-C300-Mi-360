from logging.config import fileConfig

from alembic import context

from app import models  # noqa: F401  (registers tables on Base.metadata)
from app.core.db import Base, engine

if context.config.config_file_name:
    fileConfig(context.config.config_file_name)

with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()
