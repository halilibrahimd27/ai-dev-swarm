"""Alembic environment.

Reads the connection string from :class:`aidevswarm.settings.Settings`
so migrations always use the same DSN as the running orchestrator.
Engine creation uses SQLAlchemy's ``psycopg`` (v3) driver — psycopg3 is
already a Phase 0 dependency.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import URL, engine_from_config, pool

# Alembic Config object.
config = context.config

# Standard logging via [loggers] sections in alembic.ini.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Auto-generate is not used in Phase 1 (we hand-write migrations); leave
# target_metadata None so alembic doesn't try to import an ORM module.
target_metadata = None


def _build_url() -> URL:
    """Construct an SQLAlchemy URL from aidevswarm.settings.Settings."""
    from aidevswarm.settings import Settings

    s = Settings()
    return URL.create(
        drivername="postgresql+psycopg",
        username=s.postgres_user,
        password=s.postgres_password.get_secret_value(),
        host=s.pg_host,
        port=s.pg_port,
        database=s.postgres_db,
    )


def run_migrations_offline() -> None:
    context.configure(
        url=_build_url().render_as_string(hide_password=False),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    cfg = config.get_section(config.config_ini_section, {}) or {}
    cfg["sqlalchemy.url"] = _build_url().render_as_string(hide_password=False)
    connectable = engine_from_config(
        cfg,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
