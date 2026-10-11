"""Заявки шлюза в SQLite: создание, смена статуса только по таблице переходов, история."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from leasing import SQL_DIR
from leasing.states import Status, check


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class NewApplication:
    inn: str
    company_name: str
    asset_type: str
    amount: int
    advance_percent: int
    term_months: int

    def fingerprint(self) -> str:
        raw = json.dumps(
            [self.inn, self.company_name, self.asset_type, self.amount, self.advance_percent, self.term_months],
            ensure_ascii=False,
        )
        return hashlib.sha256(raw.encode()).hexdigest()


class KeyConflict(Exception):
    """Тот же ключ идемпотентности, но другие данные заявки."""


class Store:
    def __init__(self, path: str | Path = ":memory:", *, clock: Callable[[], str] = utcnow) -> None:
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript((SQL_DIR / "schema.sql").read_text(encoding="utf-8"))
        self.clock = clock
        self._lock = threading.Lock()

    @contextmanager
    def _tx(self) -> Iterator[None]:
        self.conn.execute("BEGIN")
        try:
            yield
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")

    # ------------------------------------------------------------ чтение

    def get(self, app_id: str) -> sqlite3.Row | None:
        row: sqlite3.Row | None = self.conn.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
        return row

    def by_status(self, status: Status) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM applications WHERE status = ? ORDER BY id", (status,)))

    def history(self, app_id: str) -> list[tuple[str | None, str]]:
        return [
            (r["from_status"], r["to_status"])
            for r in self.conn.execute(
                "SELECT from_status, to_status FROM status_history WHERE application_id = ? ORDER BY id", (app_id,)
            )
        ]

    # ------------------------------------------------------------ запись

    def create(self, key: str, app: NewApplication) -> tuple[sqlite3.Row, bool]:
        """Новая заявка или уже существующая по тому же ключу: (заявка, создана ли)."""
        with self._lock:
            existing = self.conn.execute("SELECT * FROM applications WHERE idempotency_key = ?", (key,)).fetchone()
            if existing is not None:
                if existing["payload_hash"] != app.fingerprint():
                    raise KeyConflict(key)
                return existing, False
            now = self.clock()
            seq = self.conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0] + 1
            app_id = f"LA-{seq:06d}"
            with self._tx():
                self.conn.execute(
                    "INSERT INTO applications (id, idempotency_key, payload_hash, inn, company_name, asset_type, "
                    "amount, advance_percent, term_months, status, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        app_id,
                        key,
                        app.fingerprint(),
                        app.inn,
                        app.company_name,
                        app.asset_type,
                        app.amount,
                        app.advance_percent,
                        app.term_months,
                        Status.NEW,
                        now,
                        now,
                    ),
                )
                self.conn.execute(
                    "INSERT INTO status_history (application_id, from_status, to_status, at) VALUES (?, NULL, ?, ?)",
                    (app_id, Status.NEW, now),
                )
            row = self.get(app_id)
            assert row is not None  # noqa: S101 — только что вставлена
            return row, True

    def move(self, app_id: str, new: Status, *, note: str | None = None, **fields: Any) -> sqlite3.Row:
        """Сменить статус, если переход разрешён, и записать его в историю (FR-09)."""
        with self._lock:
            row = self.get(app_id)
            if row is None:
                raise KeyError(app_id)
            check(Status(row["status"]), new)
            now = self.clock()
            sets = ", ".join(f"{name} = ?" for name in fields)
            with self._tx():
                self.conn.execute(
                    f"UPDATE applications SET status = ?, updated_at = ?{', ' + sets if sets else ''} WHERE id = ?",  # noqa: S608 — имена полей из кода, не из запроса
                    (new, now, *fields.values(), app_id),
                )
                self.conn.execute(
                    "INSERT INTO status_history (application_id, from_status, to_status, at, note) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (app_id, row["status"], new, now, note),
                )
            updated = self.get(app_id)
            assert updated is not None  # noqa: S101
            return updated

    def bump_attempts(self, app_id: str) -> int:
        with self._lock:
            self.conn.execute("UPDATE applications SET attempts = attempts + 1 WHERE id = ?", (app_id,))
            return int(self.conn.execute("SELECT attempts FROM applications WHERE id = ?", (app_id,)).fetchone()[0])
