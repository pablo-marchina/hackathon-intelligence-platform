from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from hackathon_shared.settings import get_settings
from sqlalchemy import Connection, Engine, create_engine


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return create_engine(
        get_settings().database_url,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
        pool_timeout=3,
        connect_args={"connect_timeout": 3},
    )


def connection_dependency() -> Iterator[Connection]:
    with get_engine().begin() as connection:
        yield connection
