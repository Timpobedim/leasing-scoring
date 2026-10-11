"""Документация как проверяемый артефакт: диаграммы, трассировка, SRS."""

from __future__ import annotations

import re

import pytest
import yaml
from lxml import etree

from leasing import API_DIR, DOCS_DIR, diagrams, trace
from leasing.states import TRANSITIONS

WSDL_NS = "http://schemas.xmlsoap.org/wsdl/"


@pytest.mark.req("FR-09")
def test_state_diagram_equals_code() -> None:
    drawn = diagrams.state_transitions()
    coded = {(a.value, b.value) for a, b in TRANSITIONS}
    assert drawn - coded == set(), "на диаграмме есть переход, которого нет в коде"
    assert coded - drawn == set(), "в коде есть переход, которого нет на диаграмме"


def test_svg_not_stale() -> None:
    assert diagrams.stale() == []


def test_plantuml_codec_roundtrip() -> None:
    """Расшифровка вшитого исходника проверена на всех семи диаграммах."""
    for svg in sorted(diagrams.DIAGRAMS.glob("*.svg")):
        source = diagrams.embedded_source(svg)
        assert source is not None
        assert "req:" in source


def test_sequence_diagrams_cover_every_operation() -> None:
    text = "\n".join(p.read_text(encoding="utf-8") for p in diagrams.DIAGRAMS.glob("sequence-*.puml"))
    wsdl = etree.parse(str(API_DIR / "scoring.wsdl"))
    for op in wsdl.iterfind(f"{{{WSDL_NS}}}portType/{{{WSDL_NS}}}operation"):
        assert op.get("name") in text, f"SOAP-операция {op.get('name')} не показана на диаграммах"
    spec = yaml.safe_load((API_DIR / "openapi.yaml").read_text(encoding="utf-8"))
    for path, methods in spec["paths"].items():
        for method in methods:
            assert f"{method.upper()} {path}" in text, f"{method.upper()} {path} не показан на диаграммах"


def test_every_requirement_is_tested() -> None:
    assert trace.build().untested() == []


def test_no_references_to_unknown_requirements() -> None:
    assert trace.build().unknown() == []


def test_traceability_matrix_is_fresh() -> None:
    expected = trace.render(trace.build())
    assert (DOCS_DIR / "traceability.md").read_text(encoding="utf-8") == expected, "запустите leasing trace"


def test_srs_links_resolve() -> None:
    srs = (DOCS_DIR / "srs.md").read_text(encoding="utf-8")
    for target in re.findall(r"\]\((?!http|#)([^)]+)\)", srs):
        assert (DOCS_DIR / target).exists() or (DOCS_DIR.parent / target).exists(), target


def test_bank_rules_in_srs_match_code() -> None:
    """Приложение А SRS описывает те же пороги, что зашиты в эмулятор."""
    from leasing import scoring

    srs = (DOCS_DIR / "srs.md").read_text(encoding="utf-8")
    assert (
        f"Стоимость больше {scoring.BIG_DEAL // 1_000_000} млн ₽ и аванс меньше {scoring.MIN_ADVANCE_FOR_BIG_DEAL}%"
        in srs
    )
    assert f"больше {scoring.MAX_EQUIPMENT_TERM} месяцев" in srs
    for term, rate in ((24, "14,5"), (36, "15,5"), (60, "16,9")):
        assert str(scoring.rate_for(term)).replace(".", ",") == rate
        assert rate in srs
