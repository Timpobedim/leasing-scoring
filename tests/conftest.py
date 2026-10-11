from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from leasing import bank as bank_mod
from leasing import inn as inn_mod
from leasing.client import ScoringClient
from leasing.gateway import Gateway, create_app
from leasing.store import Store

INN = inn_mod.complete("771234567")


@pytest.fixture
def bank() -> bank_mod.Bank:
    return bank_mod.Bank()


@pytest.fixture
def bank_url(bank: bank_mod.Bank) -> Iterator[str]:
    server, _ = bank_mod.serve(bank, port=0)
    yield f"http://127.0.0.1:{server.server_port}/scoring"
    server.shutdown()
    server.server_close()


@pytest.fixture
def store() -> Store:
    return Store()


@pytest.fixture
def gateway(store: Store, bank_url: str) -> Gateway:
    return Gateway(store, ScoringClient(bank_url, timeout=0.5))


@pytest.fixture
def http(gateway: Gateway) -> TestClient:
    return TestClient(create_app(gateway))


def body(**changes: Any) -> dict[str, Any]:
    return {
        "inn": INN,
        "companyName": "ООО «Ромашка»",
        "assetType": "CAR",
        "amount": 3_000_000,
        "advancePercent": 20,
        "termMonths": 36,
        **changes,
    }


def key() -> str:
    return str(uuid.uuid4())


def post(http: TestClient, *, idem: str | None = None, **changes: Any) -> Any:
    return http.post("/api/v1/applications", json=body(**changes), headers={"Idempotency-Key": idem or key()})
