"""Brings a database's schema up to date with the bank's migrations (Alembic)."""

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config

MIGRATIONS = Path(__file__).parent / "migrations"


def migrate(url: str, revision: str = "head") -> None:
    command.upgrade(_config(url), revision)


def downgrade(url: str, revision: str = "base") -> None:
    command.downgrade(_config(url), revision)


async def migrate_async(url: str) -> None:
    """For callers already on an event loop: Alembic's env runs a loop of its own."""
    await asyncio.to_thread(migrate, url)


def _config(url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    config.set_main_option("sqlalchemy.url", url)
    return config
