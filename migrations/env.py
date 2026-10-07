"""Konfigurasi Alembic — URL diambil dari app.config (env DATABASE_URL)."""
from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import settings
from app.db import Base
from app import models  # noqa: F401  (registrasi semua mapper)

config = context.config
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _fulltext_untuk_mysql() -> list[str]:
    """FULLTEXT(judul_hook, naskah_md) hanya masuk saat dialeknya MySQL/MariaDB."""
    if context.get_context().dialect.name != "mysql":
        return []
    return [
        "CREATE FULLTEXT INDEX ft_content_judul_naskah "
        "ON content_item (judul_hook, naskah_md)"
    ]


def run_migrations_offline() -> None:
    context.configure(
        url=settings.DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section) or {},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )
    with connectable.connect() as connection:
        is_mysql = connection.dialect.name == "mysql"
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=True,
            # FULLTEXT tidak dikenal SQLite → tidak dibandingkan, tidak dirender ulang
            include_object=lambda obj, name, type_, reflected, compare_to: not (
                type_ == "index" and getattr(obj, "name", "") == "ft_content_judul_naskah"
            ),
        )
        with context.begin_transaction():
            context.run_migrations()
        if is_mysql:
            for ddl in _fulltext_untuk_mysql():
                try:
                    connection.exec_driver_sql(ddl)
                except Exception:  # pragma: no cover - indeks mungkin sudah ada
                    pass


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
