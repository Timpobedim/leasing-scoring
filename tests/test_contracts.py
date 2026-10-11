"""NFR-03: XSD банка, openapi.yaml и модель шлюза задают одни и те же ограничения полей.

Плюс ответы шлюза проверяются по схемам openapi.yaml — контракт и код не расходятся.
"""

from __future__ import annotations

import json
from typing import Any

import jsonschema
import pytest
import yaml
from fastapi.testclient import TestClient
from lxml import etree

from leasing import API_DIR, DOCS_DIR, scoring
from leasing.gateway import ApplicationIn

from .conftest import post

XS = "{http://www.w3.org/2001/XMLSchema}"
FIELDS = ["inn", "companyName", "assetType", "amount", "advancePercent", "termMonths"]
OPENAPI: dict[str, Any] = yaml.safe_load((API_DIR / "openapi.yaml").read_text(encoding="utf-8"))
SCHEMAS = OPENAPI["components"]["schemas"]


def _num(value: str) -> int:
    return int(value)


def xsd_constraints() -> dict[str, dict[str, Any]]:
    xsd = etree.parse(str(API_DIR / "scoring.xsd"))
    types = {t.get("name"): t for t in xsd.iterfind(f"{XS}simpleType")}
    request = xsd.find(f"{XS}element[@name='SubmitApplicationRequest']")
    assert request is not None
    out: dict[str, dict[str, Any]] = {}
    for el in request.iterfind(f".//{XS}element"):
        name, type_name = el.get("name"), (el.get("type") or "").split(":")[-1]
        if name not in FIELDS:
            continue
        restriction = types[type_name].find(f"{XS}restriction")
        assert restriction is not None
        c: dict[str, Any] = {}
        for facet in restriction:
            tag, value = etree.QName(facet).localname, facet.get("value")
            if tag == "pattern":
                c["pattern"] = f"^{value}$"
            elif tag == "enumeration":
                c.setdefault("enum", []).append(value)
            elif tag == "minInclusive":
                c["minimum"] = _num(value)
            elif tag == "maxInclusive":
                c["maximum"] = _num(value)
            elif tag in ("minLength", "maxLength"):
                c[tag] = _num(value)
        out[name] = c
    return out


def openapi_constraints() -> dict[str, dict[str, Any]]:
    keys = ("pattern", "enum", "minimum", "maximum", "minLength", "maxLength")
    props = SCHEMAS["ApplicationIn"]["properties"]
    return {name: {k: props[name][k] for k in keys if k in props[name]} for name in FIELDS}


def model_constraints() -> dict[str, dict[str, Any]]:
    keys = ("pattern", "enum", "minimum", "maximum", "minLength", "maxLength")
    props = ApplicationIn.model_json_schema(by_alias=True)["properties"]
    return {name: {k: props[name][k] for k in keys if k in props[name]} for name in FIELDS}


@pytest.mark.req("NFR-03")
@pytest.mark.parametrize("field", FIELDS)
def test_three_contracts_agree(field: str) -> None:
    xsd, api, model = xsd_constraints()[field], openapi_constraints()[field], model_constraints()[field]
    assert xsd == api, f"XSD и openapi.yaml расходятся по {field}"
    assert api == model, f"openapi.yaml и модель шлюза расходятся по {field}"


@pytest.mark.req("NFR-03")
def test_required_fields_agree() -> None:
    assert set(SCHEMAS["ApplicationIn"]["required"]) == set(FIELDS)
    assert set(ApplicationIn.model_json_schema(by_alias=True)["required"]) == set(FIELDS)


def test_reason_codes_agree() -> None:
    xsd = etree.parse(str(API_DIR / "scoring.xsd"))
    codes = {e.get("value") for e in xsd.iterfind(f"{XS}simpleType[@name='ReasonCode']//{XS}enumeration")}
    api = set(SCHEMAS["Decision"]["properties"]["reasonCode"]["enum"]) - {None}
    assert codes == api
    # и все причины, которые выдают правила банка, описаны в контракте
    assert {"STOP_LIST", "ADVANCE_TOO_LOW", "TERM_TOO_LONG"} == codes
    assert (
        scoring.decide(
            scoring.Application("r", sorted(scoring.STOP_LIST)[0], "x", "CAR", 1_000_000, 20, 12)
        ).reason_code
        in codes
    )


def _validator(name: str) -> jsonschema.protocols.Validator:
    schema = {"$ref": f"#/components/schemas/{name}", "components": OPENAPI["components"]}
    return jsonschema.Draft202012Validator(schema)


@pytest.mark.req("FR-01")
@pytest.mark.req("FR-08")
def test_responses_match_openapi(http: TestClient) -> None:
    created = post(http)
    _validator("ApplicationOut").validate(created.json())
    _validator("ApplicationOut").validate(http.get(f"/api/v1/applications/{created.json()['id']}").json())
    _validator("Error").validate(post(http, amount=1).json())
    _validator("Error").validate(http.get("/api/v1/applications/LA-999999").json())


@pytest.mark.parametrize(
    ("example", "schema"),
    [
        ("application-in.json", "ApplicationIn"),
        ("application-out-approved.json", "ApplicationOut"),
        ("error-validation.json", "Error"),
    ],
)
def test_json_examples_match_openapi(example: str, schema: str) -> None:
    _validator(schema).validate(json.loads((DOCS_DIR / "examples" / example).read_text(encoding="utf-8")))


def test_example_request_accepted_by_gateway(http: TestClient) -> None:
    body = json.loads((DOCS_DIR / "examples" / "application-in.json").read_text(encoding="utf-8"))
    import uuid

    response = http.post("/api/v1/applications", json=body, headers={"Idempotency-Key": str(uuid.uuid4())})
    assert response.status_code == 202


def test_gateway_publishes_only_contract_paths(http: TestClient) -> None:
    """Контракт первичен: шлюз не публикует путей и методов, которых нет в openapi.yaml."""
    generated = http.get("/openapi.json").json()["paths"]
    declared = {(path, method) for path, ops in OPENAPI["paths"].items() for method in ops}
    actual = {(path.replace("{app_id}", "{id}"), method) for path, ops in generated.items() for method in ops}
    assert actual == declared
