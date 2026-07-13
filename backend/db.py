"""Вспомогательные функции для работы backend-а с PostgreSQL."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable

import psycopg2
from psycopg2.extras import RealDictCursor

from .config import settings


def get_connection():
    """Открывает соединение с PostgreSQL."""

    return psycopg2.connect(settings.database_url)


def fetch_one(conn, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
    """Выполняет SELECT-запрос и возвращает одну строку или None."""

    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute(sql, tuple(params))
        row = cursor.fetchone()
        return normalize(dict(row)) if row else None
    finally:
        cursor.close()


def fetch_all(conn, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    """Выполняет SELECT-запрос и возвращает список строк."""

    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute(sql, tuple(params))
        return [normalize(dict(row)) for row in cursor.fetchall()]
    finally:
        cursor.close()


def execute_one(conn, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
    """Выполняет INSERT/UPDATE ... RETURNING и возвращает одну строку."""

    return fetch_one(conn, sql, params)


def normalize(value: Any) -> Any:
    """Приводит значения PostgreSQL к JSON виду.

    psycopg2 возвращает NUMERIC как Decimal, а даты как datetime. Явная
    нормализация сохраняет предсказуемый JSON-контракт API.
    """

    if isinstance(value, Decimal):
        return float(value)

    if isinstance(value, (datetime, date)):
        return value.isoformat()

    if isinstance(value, list):
        return [normalize(item) for item in value]

    if isinstance(value, dict):
        return {key: normalize(item) for key, item in value.items()}

    return value
