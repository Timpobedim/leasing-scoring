"""Шлюз по HTTP: требования FR-01…FR-09 с эмулятором банка на настоящем сокете."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from leasing import scoring
from leasing.bank import Bank
from leasing.gateway import Gateway
from leasing.states import Status
from leasing.store import Store

from .conftest import key, post


@pytest.mark.req("FR-01")
@pytest.mark.req("FR-04")
def test_application_accepted_and_sent(http: TestClient, bank: Bank, store: Store) -> None:
    response = post(http)
    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "SENT"
    assert data["bankId"] == "SC-00000001"
    assert data["attempts"] == 1
    assert bank.calls == ["SubmitApplication"]
    assert store.history(data["id"]) == [(None, "NEW"), ("NEW", "SENT")]


@pytest.mark.req("FR-02")
@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"amount": 99_999}, "amount"),
        ({"amount": 50_000_001}, "amount"),
        ({"advancePercent": 9}, "advancePercent"),
        ({"advancePercent": 50}, "advancePercent"),
        ({"termMonths": 61}, "termMonths"),
        ({"assetType": "BOAT"}, "assetType"),
        ({"companyName": ""}, "companyName"),
        ({"companyName": "x" * 201}, "companyName"),
        ({"inn": "77123456"}, "inn"),
        ({"inn": "7712345670"}, "inn"),  # контрольная цифра
        ({"extra": 1}, "extra"),
    ],
)
def test_bad_field_rejected_before_bank(http: TestClient, bank: Bank, change: dict[str, object], field: str) -> None:
    response = post(http, **change)
    assert response.status_code == 422
    assert response.json()["field"] == field
    assert bank.calls == []


@pytest.mark.req("FR-02")
@pytest.mark.parametrize("header", [None, "not-a-uuid"])
def test_idempotency_key_required(http: TestClient, bank: Bank, header: str | None) -> None:
    from .conftest import body

    headers = {"Idempotency-Key": header} if header else {}
    response = http.post("/api/v1/applications", json=body(), headers=headers)
    assert response.status_code == 422
    assert response.json()["field"] == "Idempotency-Key"
    assert bank.calls == []


@pytest.mark.req("FR-03")
def test_same_key_same_data_returns_same_application(http: TestClient, bank: Bank) -> None:
    idem = key()
    first, second = post(http, idem=idem).json(), post(http, idem=idem).json()
    assert first == second
    assert bank.calls == ["SubmitApplication"]  # повтор до банка не дошёл


@pytest.mark.req("FR-03")
def test_same_key_other_data_conflicts(http: TestClient) -> None:
    idem = key()
    post(http, idem=idem)
    response = post(http, idem=idem, amount=4_000_000)
    assert response.status_code == 409
    assert response.json()["error"] == "conflict"


@pytest.mark.req("FR-05")
def test_bank_validation_fault_marks_invalid(http: TestClient, store: Store) -> None:
    response = post(http, inn=sorted(scoring.UNREGISTERED)[0])
    assert response.status_code == 422
    assert response.json() == {"error": "validation", "field": "inn", "message": "ИНН не найден в ЕГРЮЛ"}
    (row,) = store.by_status(Status.INVALID)
    assert row["error_field"] == "inn"


@pytest.mark.req("FR-06")
def test_bank_down_waits_and_retry_sends(http: TestClient, bank: Bank, gateway: Gateway, store: Store) -> None:
    bank.mode = "down"
    data = post(http).json()
    assert data["status"] == "WAITING_RETRY"
    assert data["bankId"] is None
    bank.mode = "ok"
    (row,) = gateway.retry_pending()
    assert row["status"] == Status.SENT
    assert row["attempts"] == 2
    assert store.history(data["id"]) == [(None, "NEW"), ("NEW", "WAITING_RETRY"), ("WAITING_RETRY", "SENT")]


@pytest.mark.req("FR-06")
@pytest.mark.req("NFR-01")
def test_late_answer_does_not_duplicate_in_bank(http: TestClient, bank: Bank, gateway: Gateway) -> None:
    """Банк принял заявку, но ответил после таймаута: повтор с тем же requestId дубля не создаёт."""
    bank.mode, bank.slow_seconds = "slow", 1.0
    data = post(http).json()
    assert data["status"] == "WAITING_RETRY"
    assert bank.count() == 1  # заявка в банке уже есть
    bank.mode = "ok"
    (row,) = gateway.retry_pending()
    assert row["status"] == Status.SENT
    assert row["bank_id"] == "SC-00000001"
    assert bank.count() == 1


@pytest.mark.req("FR-06")
def test_attempts_run_out(http: TestClient, bank: Bank, gateway: Gateway) -> None:
    bank.mode = "down"
    post(http).json()
    gateway.retry_pending()
    (row,) = gateway.retry_pending()
    assert row["status"] == Status.FAILED
    assert row["attempts"] == 3
    assert gateway.retry_pending() == []


@pytest.mark.req("FR-07")
@pytest.mark.req("FR-08")
def test_decision_approved(http: TestClient) -> None:
    app_id = post(http).json()["id"]
    data = http.get(f"/api/v1/applications/{app_id}").json()
    assert data["status"] == "APPROVED"
    assert data["decision"] == {"approvedAmount": 2_400_000, "ratePercent": 15.5, "reasonCode": None}


@pytest.mark.req("FR-08")
@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"inn": sorted(scoring.STOP_LIST)[0]}, "STOP_LIST"),
        ({"amount": 12_000_000, "advancePercent": 15}, "ADVANCE_TOO_LOW"),
        ({"assetType": "EQUIPMENT", "termMonths": 48}, "TERM_TOO_LONG"),
    ],
)
def test_decision_declined_with_reason(http: TestClient, change: dict[str, object], reason: str) -> None:
    app_id = post(http, **change).json()["id"]
    data = http.get(f"/api/v1/applications/{app_id}").json()
    assert data["status"] == "DECLINED"
    assert data["decision"]["reasonCode"] == reason


@pytest.mark.req("FR-07")
def test_in_review_keeps_sent(http: TestClient, bank: Bank) -> None:
    bank.review_seconds = 60
    app_id = post(http).json()["id"]
    assert http.get(f"/api/v1/applications/{app_id}").json()["status"] == "SENT"
    assert bank.calls == ["SubmitApplication", "GetDecision"]


@pytest.mark.req("FR-07")
def test_bank_down_on_status_keeps_status(http: TestClient, bank: Bank) -> None:
    app_id = post(http).json()["id"]
    bank.mode = "down"
    assert http.get(f"/api/v1/applications/{app_id}").json()["status"] == "SENT"


@pytest.mark.req("FR-07")
def test_unknown_application_404(http: TestClient) -> None:
    response = http.get("/api/v1/applications/LA-999999")
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


@pytest.mark.req("FR-07")
def test_final_status_does_not_call_bank(http: TestClient, bank: Bank) -> None:
    app_id = post(http).json()["id"]
    http.get(f"/api/v1/applications/{app_id}")
    calls = len(bank.calls)
    assert http.get(f"/api/v1/applications/{app_id}").json()["status"] == "APPROVED"
    assert len(bank.calls) == calls


@pytest.mark.req("FR-02")
@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"amount": 99_999}, "не меньше 100000"),
        ({"termMonths": 61}, "не больше 60"),
        ({"assetType": "BOAT"}, "допустимо: 'CAR', 'TRUCK', 'EQUIPMENT'"),
        ({"companyName": ""}, "не короче 1 символов"),
        ({"extra": 1}, "лишнее поле"),
        ({"amount": "много"}, "нужно целое число"),
    ],
)
def test_validation_messages_in_russian(http: TestClient, change: dict[str, object], message: str) -> None:
    assert post(http, **change).json()["message"] == message
