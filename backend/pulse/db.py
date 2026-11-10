"""Raw read queries. Each call runs in its own read-only transaction (the role is read-only too)."""

from __future__ import annotations

from django.db import connection, transaction

TORONTO = "America/Toronto"
SYNTHETIC_TG = 9_000_000_000_000  # imported courier people have no Telegram account behind them


def rows(sql: str, params: list | tuple | dict | None = None) -> list[dict]:
    with transaction.atomic(), connection.cursor() as cursor:
        if connection.vendor == "postgresql":
            cursor.execute("SET TRANSACTION READ ONLY")
        cursor.execute(sql, params or [])
        if cursor.description is None:
            return []
        names = [col[0] for col in cursor.description]
        return [dict(zip(names, row)) for row in cursor.fetchall()]


def one(sql: str, params: list | tuple | dict | None = None) -> dict:
    found = rows(sql, params)
    return found[0] if found else {}


def value(sql: str, params: list | tuple | dict | None = None, default=0):
    found = one(sql, params)
    return next(iter(found.values()), default) if found else default
