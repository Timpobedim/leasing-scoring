"""FR-10: SQL-отчёты на заранее известных данных — ответ посчитан руками."""

from __future__ import annotations

import pytest

from leasing import reports
from leasing.states import Status
from leasing.store import NewApplication, Store

NOW = "2026-10-10T12:00:00+00:00"


def _app(store: Store, n: int, asset: str, path: list[tuple[Status, str]], **fields: object) -> None:
    times = iter([t for _, t in path])
    store.clock = lambda: next(times)
    data = NewApplication(f"77000000{n:02d}", f"Компания {n}", asset, 1_000_000, 20, 24)
    row, _ = store.create(f"key-{n}", data)
    for status, _ in path[1:]:
        extra = fields if status in (Status.APPROVED, Status.DECLINED) else {}
        if status == Status.SENT:
            extra = {"bank_id": f"SC-{n:08d}"}
        store.move(row["id"], status, **extra)


@pytest.fixture
def seeded() -> Store:
    store = Store()
    t = "2026-10-0{}T{:02d}:00:00+00:00".format
    # CAR: два одобрения, один отказ → 66,7 %
    _app(store, 1, "CAR", [(Status.NEW, t(1, 9)), (Status.SENT, t(1, 9)), (Status.APPROVED, t(1, 11))])
    _app(store, 2, "CAR", [(Status.NEW, t(1, 9)), (Status.SENT, t(1, 10)), (Status.APPROVED, t(1, 14))])
    _app(
        store,
        3,
        "CAR",
        [(Status.NEW, t(2, 9)), (Status.SENT, t(2, 9)), (Status.DECLINED, t(2, 15))],
        reason_code="STOP_LIST",
    )
    # TRUCK: отказ после повтора — время считается от последней отправки
    _app(
        store,
        4,
        "TRUCK",
        [(Status.NEW, t(3, 9)), (Status.WAITING_RETRY, t(3, 9)), (Status.SENT, t(3, 12)), (Status.DECLINED, t(3, 18))],
        reason_code="ADVANCE_TOO_LOW",
    )
    # Зависшие: SENT 9-го в 10:00 (26 ч к NOW) и SENT 10-го в 08:00 (4 ч — ещё не зависла)
    _app(store, 5, "EQUIPMENT", [(Status.NEW, "2026-10-09T10:00:00+00:00"), (Status.SENT, "2026-10-09T10:00:00+00:00")])
    _app(store, 6, "EQUIPMENT", [(Status.NEW, "2026-10-10T08:00:00+00:00"), (Status.SENT, "2026-10-10T08:00:00+00:00")])
    # Ошибка до банка в отчёты решений не попадает
    _app(store, 7, "CAR", [(Status.NEW, t(4, 9)), (Status.INVALID, t(4, 9))])
    return store


@pytest.mark.req("FR-10")
def test_approval_by_asset(seeded: Store) -> None:
    assert reports.run(seeded.conn, "approval_by_asset") == [
        {"asset_type": "CAR", "decided": 3, "approved": 2, "approval_pct": 66.7},
        {"asset_type": "TRUCK", "decided": 1, "approved": 0, "approval_pct": 0.0},
    ]


@pytest.mark.req("FR-10")
def test_decision_time(seeded: Store) -> None:
    # 2 ч, 4 ч, 6 ч, 6 ч (от повторной отправки, не от первой попытки)
    assert reports.run(seeded.conn, "decision_time") == [{"decided": 4, "avg_hours": 4.5, "max_hours": 6.0}]


@pytest.mark.req("FR-10")
def test_stuck_in_review(seeded: Store) -> None:
    assert reports.run(seeded.conn, "stuck_in_review", now=NOW) == [
        {"id": "LA-000005", "company_name": "Компания 5", "hours_waiting": 26.0}
    ]


@pytest.mark.req("FR-10")
def test_decline_reasons(seeded: Store) -> None:
    assert reports.run(seeded.conn, "decline_reasons") == [
        {"reason_code": "ADVANCE_TOO_LOW", "declined": 1},
        {"reason_code": "STOP_LIST", "declined": 1},
    ]


def test_reports_are_named() -> None:
    assert list(reports.queries()) == ["approval_by_asset", "decision_time", "stuck_in_review", "decline_reasons"]
