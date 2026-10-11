"""Матрица трассировки: требование → диаграммы → операции API → тесты.

Ссылки собираются из самих артефактов, а не ведутся руками:
- требования — заголовки `### FR-01.` в docs/srs.md;
- диаграммы — комментарий `' req: FR-01 FR-02` в .puml;
- REST — номера требований в `summary` операций openapi.yaml;
- SOAP — номера в `<wsdl:documentation>` операций scoring.wsdl;
- тесты — маркер `@pytest.mark.req("FR-01")`.
"""

from __future__ import annotations

import ast
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from lxml import etree

from leasing import API_DIR, DOCS_DIR, ROOT

REQ = re.compile(r"\b(?:FR|NFR)-\d{2}\b")
_HEADING = re.compile(r"^### ((?:FR|NFR)-\d{2})\. (.+)$", re.MULTILINE)
WSDL_NS = "http://schemas.xmlsoap.org/wsdl/"


@dataclass(slots=True)
class Matrix:
    titles: dict[str, str]
    diagrams: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    api: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    tests: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))

    def untested(self) -> list[str]:
        return [req for req in self.titles if not self.tests.get(req)]

    def unknown(self) -> list[str]:
        """Ссылки на требования, которых нет в SRS: опечатка или удалённое требование."""
        used = set(self.diagrams) | set(self.api) | set(self.tests)
        return sorted(used - set(self.titles))


def requirements(srs: Path = DOCS_DIR / "srs.md") -> dict[str, str]:
    return dict(_HEADING.findall(srs.read_text(encoding="utf-8")))


def _test_refs(tests_dir: Path) -> dict[str, list[str]]:
    refs: dict[str, list[str]] = defaultdict(list)
    for path in sorted(tests_dir.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for deco in node.decorator_list:
                if isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute) and deco.func.attr == "req":
                    for arg in deco.args:
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            refs[arg.value].append(f"{path.stem}::{node.name}")
    return refs


def build(tests_dir: Path = ROOT / "tests") -> Matrix:
    matrix = Matrix(requirements())
    for puml in sorted((DOCS_DIR / "diagrams").glob("*.puml")):
        for line in puml.read_text(encoding="utf-8").splitlines():
            if line.startswith("' req:"):
                for req in REQ.findall(line):
                    matrix.diagrams[req].append(puml.stem)
    spec = yaml.safe_load((API_DIR / "openapi.yaml").read_text(encoding="utf-8"))
    for path, methods in spec["paths"].items():
        for method, op in methods.items():
            for req in REQ.findall(op.get("summary", "")):
                matrix.api[req].append(f"{method.upper()} {path}")
    wsdl = etree.parse(str(API_DIR / "scoring.wsdl"))
    for op in wsdl.iterfind(f"{{{WSDL_NS}}}portType/{{{WSDL_NS}}}operation"):
        for req in REQ.findall(op.findtext(f"{{{WSDL_NS}}}documentation") or ""):
            matrix.api[req].append(f"SOAP {op.get('name')}")
    matrix.tests.update(_test_refs(tests_dir))
    return matrix


def render(matrix: Matrix) -> str:
    lines = [
        "# Матрица трассировки",
        "",
        "Строится командой `leasing trace` из docs/srs.md, диаграмм, openapi.yaml, scoring.wsdl и тестов.",
        "Руками не правится: тест сверяет файл с тем, что строится из артефактов.",
        "",
        "| Требование | Диаграммы | API | Тесты |",
        "|---|---|---|---|",
    ]
    for req, title in matrix.titles.items():
        diagrams = ", ".join(sorted(set(matrix.diagrams.get(req, [])))) or "—"
        api = ", ".join(sorted(set(matrix.api.get(req, [])))) or "—"
        tests = matrix.tests.get(req, [])
        lines.append(
            f"| **{req}** {title} | {diagrams} | {api} | {len(tests)}: "
            + ("<br>".join(f"`{t}`" for t in sorted(tests)) or "—")
            + " |"
        )
    return "\n".join(lines) + "\n"
