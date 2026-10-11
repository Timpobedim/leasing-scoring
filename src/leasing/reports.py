"""SQL-отчёты из sql/reports.sql (FR-10)."""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from leasing import SQL_DIR
from leasing.store import utcnow

_NAME = re.compile(r"^-- name: (\w+)\s*$", re.MULTILINE)


def queries() -> dict[str, str]:
    text = (SQL_DIR / "reports.sql").read_text(encoding="utf-8")
    parts = _NAME.split(text)[1:]
    return {name: sql.strip() for name, sql in zip(parts[::2], parts[1::2], strict=True)}


def run(conn: sqlite3.Connection, name: str, *, now: str | None = None) -> list[dict[str, Any]]:
    sql = queries()[name]
    params = {"now": now or utcnow()} if ":now" in sql else {}
    cursor = conn.execute(sql, params)
    columns = [d[0] for d in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
