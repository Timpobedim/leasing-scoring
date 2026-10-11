"""ИНН, правила банка, жизненный цикл и хранилище — без сети."""

from __future__ import annotations

from decimal import Decimal

import pytest

from leasing import inn, scoring
from leasing.states import FINAL, TRANSITIONS, IllegalTransition, Status
from leasing.store import KeyConflict, NewApplication, Store

DATA = NewApplication("7712345671", "ООО Ромашка", "CAR", 3_000_000, 20, 36)


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        ("7712345671", True),
        ("7707083893", True),
        ("7712345670", False),
        ("771234567", False),
        ("77123456711", False),
        ("77123456a1", False),
        ("７７１２３４５６７１", False),
    ],
)
def test_inn(value: str, ok: bool) -> None:
    assert inn.is_valid(value) is ok


def test_complete_builds_valid_inn() -> None:
    assert all(inn.is_valid(inn.complete(f"{n:09d}")) for n in range(0, 10_000, 37))


def _app(**changes: object) -> scoring.Application:
    base: dict[str, object] = {
        "request_id": "r",
        "inn": "7712345671",
        "company_name": "x",
        "asset_type": "CAR",
        "amount": 3_000_000,
        "advance_percent": 20,
        "term_months": 36,
    }
    return scoring.Application(**{**base, **changes})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("changes", "status", "reason"),
    [
        ({}, "APPROVED", None),
        ({"inn": sorted(scoring.STOP_LIST)[0]}, "DECLINED", "STOP_LIST"),
        ({"amount": 10_000_001, "advance_percent": 19}, "DECLINED", "ADVANCE_TOO_LOW"),
        ({"amount": 10_000_000, "advance_percent": 10}, "APPROVED", None),  # граница: «больше 10 млн»
        ({"amount": 10_000_001, "advance_percent": 20}, "APPROVED", None),  # граница: «меньше 20 %»
        ({"asset_type": "EQUIPMENT", "term_months": 37}, "DECLINED", "TERM_TOO_LONG"),
        ({"asset_type": "EQUIPMENT", "term_months": 36}, "APPROVED", None),
        ({"asset_type": "TRUCK", "term_months": 60}, "APPROVED", None),
    ],
)
def test_bank_rules(changes: dict[str, object], status: str, reason: str | None) -> None:
    decision = scoring.decide(_app(**changes))
    assert (decision.status, decision.reason_code) == (status, reason)


def test_approved_amount_is_financed_part() -> None:
    decision = scoring.decide(_app(amount=1_234_567, advance_percent=15, term_months=24))
    assert decision.approved_amount == 1_049_382  # 1 234 567 × 0,85 = 1 049 381,95 → округление вверх
    assert decision.rate_percent == Decimal("14.5")


@pytest.mark.req("FR-09")
def test_illegal_transition_rejected_and_history_untouched() -> None:
    store = Store()
    row, _ = store.create("k", DATA)
    with pytest.raises(IllegalTransition):
        store.move(row["id"], Status.APPROVED)  # из NEW сразу в одобрение нельзя
    assert store.get(row["id"])["status"] == Status.NEW  # type: ignore[index]
    assert store.history(row["id"]) == [(None, "NEW")]


@pytest.mark.req("FR-09")
def test_final_statuses_have_no_exit() -> None:
    assert not [t for t in TRANSITIONS if t[0] in FINAL]


@pytest.mark.req("FR-03")
def test_store_idempotency() -> None:
    store = Store()
    first, created = store.create("k", DATA)
    again, created_again = store.create("k", DATA)
    assert created
    assert not created_again
    assert first["id"] == again["id"]
    with pytest.raises(KeyConflict):
        store.create("k", NewApplication("7712345671", "ООО Ромашка", "CAR", 3_000_001, 20, 36))


def test_store_rejects_out_of_range_even_without_gateway() -> None:
    """CHECK в схеме — последняя линия: данные в обход шлюза тоже проверяются."""
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        Store().create("k", NewApplication("7712345671", "x", "CAR", 1, 20, 36))
