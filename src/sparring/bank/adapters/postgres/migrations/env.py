"""Alembic's entry point: runs the migrations on an async engine."""

import asyncio

from alembic import context
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from sparring.bank.adapters.postgres.schema import is_the_bank_s, metadata


def run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=metadata, include_name=is_the_bank_s)
    with context.begin_transaction():
        context.run_migrations()


async def main() -> None:
    engine = create_async_engine(context.config.get_main_option("sqlalchemy.url") or "")
    async with engine.connect() as connection:
        await connection.run_sync(run)
    await engine.dispose()


asyncio.run(main())
