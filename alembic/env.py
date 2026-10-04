import importlib
import os
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# --- Base import (projene göre fallback'li) ---
Base = None
for mod_name in ("app.db.base", "app.db.database", "app.database"):
    try:
        mod = importlib.import_module(mod_name)
        Base = getattr(mod, "Base", None)
        if Base is not None:
            break
    except Exception:
        pass

if Base is None:
    raise RuntimeError("Base bulunamadı. app/db/base.py veya eşdeğerinde Base tanımlı olmalı.")

# --- Model modüllerini import et (autogenerate için) ---
# app altındaki tüm py dosyalarını yüklemeye çalışıyoruz; hata verenleri geçiyoruz.
app_dir = Path("app")
for py in app_dir.rglob("*.py"):
    if py.name == "__init__.py":
        continue
    mod = str(py.with_suffix("")).replace("/", ".")
    try:
        importlib.import_module(mod)
    except Exception:
        pass

target_metadata = Base.metadata


def get_database_url() -> str:
    return os.getenv("DATABASE_URL") or os.getenv("SQLALCHEMY_DATABASE_URL") or ""


def run_migrations_offline() -> None:
    url = get_database_url()
    if not url:
        raise RuntimeError("DATABASE_URL/SQLALCHEMY_DATABASE_URL yok.")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = get_database_url()
    if not url:
        raise RuntimeError("DATABASE_URL/SQLALCHEMY_DATABASE_URL yok.")

    cfg = config.get_section(config.config_ini_section) or {}
    cfg["sqlalchemy.url"] = url

    connectable = engine_from_config(
        cfg,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
