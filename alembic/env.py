from logging.config import fileConfig

from sqlalchemy import engine_from_config, inspect, text
from sqlalchemy import pool

from alembic import context
from alembic.script import ScriptDirectory

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata


import sys
import os
from dotenv import load_dotenv

load_dotenv()
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from app.db import Base
from app import models


target_metadata = Base.metadata

database_url = os.getenv("DATABASE_URL")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))


def include_object(object, name, type_, reflected, compare_to):
    """Exclude runtime-generated dataset tables and the current_dataset view from Alembic autogenerate."""
    if type_ == "table" and name.startswith("dataset_"):
        return False
    if type_ == "view" and name == "current_dataset":
        return False
    return True


# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        # The original initial revision is empty because the app historically
        # created its schema with Base.metadata.create_all(). Bootstrap only a
        # truly empty database, preserving normal migrations for existing DBs.
        inspector = inspect(connection)
        user_tables = [
            name for name in inspector.get_table_names()
            if name != "alembic_version"
        ]
        version_rows = []
        if "alembic_version" in inspector.get_table_names():
            version_rows = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalars().all()
        interrupted_bootstrap = (
            version_rows == ["bc824a76dd00"]
        )
        if not user_tables or interrupted_bootstrap:
            Base.metadata.create_all(bind=connection)
            heads = ScriptDirectory.from_config(config).get_heads()
            if len(heads) != 1:
                raise RuntimeError("Expected exactly one Alembic head for fresh database bootstrap")
            if "alembic_version" not in inspector.get_table_names():
                connection.execute(
                    text(
                        "CREATE TABLE alembic_version ("
                        "version_num VARCHAR(32) NOT NULL, "
                        "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)"
                        ")"
                    )
                )
                connection.execute(
                    text("INSERT INTO alembic_version (version_num) VALUES (:head)"),
                    {"head": heads[0]},
                )
            else:
                connection.execute(
                    text("UPDATE alembic_version SET version_num = :head"),
                    {"head": heads[0]},
                )
            connection.commit()
            return

        # The inspection queries above autobegin a transaction; end it so Alembic
        # opens (and commits) its own. Otherwise the final alembic_version update
        # is rolled back when the connection closes.
        if connection.in_transaction():
            connection.commit()

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()