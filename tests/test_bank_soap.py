"""Эмулятор банка по SOAP «сырыми» запросами: контракт, Fault, безопасность разбора XML."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from lxml import etree

from leasing import DOCS_DIR, NS
from leasing.bank import MAX_BODY, SOAP_ENV, Bank

EXAMPLES = DOCS_DIR / "examples"


def envelope(payload: str) -> bytes:
    return (
        f'<soap:Envelope xmlns:soap="{SOAP_ENV}" xmlns:tns="{NS}"><soap:Body>{payload}</soap:Body></soap:Envelope>'
    ).encode()


def submit_xml(**changes: str) -> str:
    fields = {
        "requestId": "3f2b8c1e-5d7a-4e9b-9c1d-2a6f8e0b7c4d",
        "inn": "7712345671",
        "companyName": "ООО Ромашка",
        "assetType": "CAR",
        "amount": "3000000",
        "advancePercent": "20",
        "termMonths": "36",
        **changes,
    }
    inner = "".join(f"<tns:{k}>{v}</tns:{k}>" for k, v in fields.items() if v is not None)
    return f"<tns:SubmitApplicationRequest>{inner}</tns:SubmitApplicationRequest>"


def call(url: str, payload: str | bytes) -> tuple[int, etree._Element]:
    data = payload if isinstance(payload, bytes) else envelope(payload)
    response = httpx.post(url, content=data, headers={"Content-Type": "text/xml; charset=utf-8"})
    return response.status_code, etree.fromstring(response.content)


def fault(root: etree._Element) -> dict[str, str | None]:
    node = root.find(f".//{{{NS}}}ScoringFault")
    assert node is not None
    return {name: node.findtext(f"{{{NS}}}{name}") for name in ("code", "field", "message")}


@pytest.mark.req("FR-04")
def test_submit_returns_received(bank_url: str) -> None:
    status, root = call(bank_url, submit_xml())
    assert status == 200
    assert root.findtext(f".//{{{NS}}}applicationId") == "SC-00000001"
    assert root.findtext(f".//{{{NS}}}duplicate") == "false"


@pytest.mark.req("FR-06")
def test_same_request_id_is_duplicate(bank_url: str, bank: Bank) -> None:
    call(bank_url, submit_xml())
    _, root = call(bank_url, submit_xml())
    assert root.findtext(f".//{{{NS}}}duplicate") == "true"
    assert bank.count() == 1


def test_same_request_id_other_data_conflict(bank_url: str) -> None:
    call(bank_url, submit_xml())
    status, root = call(bank_url, submit_xml(amount="4000000"))
    assert status == 500
    assert fault(root)["code"] == "CONFLICT"


@pytest.mark.req("FR-05")
@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"amount": "99999"}, "amount"),
        ({"assetType": "BOAT"}, "assetType"),
        ({"inn": "77123"}, "inn"),
        ({"termMonths": None}, "termMonths"),  # обязательного поля нет
        ({"inn": "7712345670"}, "inn"),  # контрольная цифра — проверка сервиса, не схемы
    ],
)
def test_validation_fault_names_field(bank_url: str, bank: Bank, change: dict[str, str | None], field: str) -> None:
    status, root = call(bank_url, submit_xml(**change))  # type: ignore[arg-type]
    assert status == 500
    assert root.findtext(".//faultcode") == "soap:Client"
    assert fault(root)["code"] == "VALIDATION"
    assert fault(root)["field"] == field
    assert bank.count() == 0


def test_decision_not_found(bank_url: str) -> None:
    status, root = call(
        bank_url, "<tns:GetDecisionRequest><tns:applicationId>SC-00000099</tns:applicationId></tns:GetDecisionRequest>"
    )
    assert status == 500
    assert fault(root)["code"] == "NOT_FOUND"


def test_unknown_operation(bank_url: str) -> None:
    status, root = call(bank_url, "<tns:DropDatabase/>")
    assert status == 500
    assert "неизвестная операция" in (fault(root)["message"] or "")


@pytest.mark.req("NFR-02")
def test_xxe_is_not_resolved(bank_url: str, tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET")
    body = (
        f'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY xxe SYSTEM "{secret.as_uri()}">]>'
        f'<soap:Envelope xmlns:soap="{SOAP_ENV}" xmlns:tns="{NS}"><soap:Body>'
        + submit_xml(companyName="&xxe;")
        + "</soap:Body></soap:Envelope>"
    ).encode()
    response = httpx.post(bank_url, content=body)
    assert response.status_code == 500
    assert b"TOP-SECRET" not in response.content
    assert "DTD" in (fault(etree.fromstring(response.content))["message"] or "")


@pytest.mark.req("NFR-02")
def test_oversized_body_rejected(bank_url: str, bank: Bank) -> None:
    response = httpx.post(bank_url, content=envelope(submit_xml(companyName="x" * MAX_BODY)))
    assert response.status_code == 413
    assert bank.calls == []


def test_not_xml(bank_url: str) -> None:
    status, root = call(bank_url, b"not xml at all")
    assert status == 500
    assert fault(root)["code"] == "VALIDATION"


def test_wsdl_and_xsd_served(bank_url: str) -> None:
    wsdl = httpx.get(bank_url + "?wsdl")
    assert wsdl.status_code == 200
    assert b"ScoringBinding" in wsdl.content
    xsd = httpx.get(bank_url + ".xsd")
    assert xsd.status_code == 200
    assert b"SubmitApplicationRequest" in xsd.content


def test_down_mode_is_503_without_fault(bank_url: str, bank: Bank) -> None:
    bank.mode = "down"
    response = httpx.post(bank_url, content=envelope(submit_xml()))
    assert response.status_code == 503
    assert bank.calls == []


@pytest.mark.parametrize("example", sorted(p.name for p in EXAMPLES.glob("*.xml")))
def test_examples_match_schema(example: str) -> None:
    """Примеры из документации — по схеме: пример в документе не может устареть незаметно."""
    from leasing.bank import _SCHEMA

    root = etree.parse(str(EXAMPLES / example)).getroot()
    payload = root.find(f"{{{SOAP_ENV}}}Body")[0]  # type: ignore[index]
    if payload.tag == f"{{{SOAP_ENV}}}Fault":
        payload = payload.find(f"detail/{{{NS}}}ScoringFault")
    assert _SCHEMA.validate(etree.ElementTree(payload)), _SCHEMA.error_log


def test_bank_accepts_its_own_example(bank_url: str) -> None:
    status, _ = call(bank_url, (EXAMPLES / "submit-request.xml").read_bytes())
    assert status == 200
