"""Диаграммы PlantUML: исходник, отрисовка и проверка, что SVG не отстал от исходника.

PlantUML вшивает текст диаграммы в SVG (`<?plantuml-src …?>`): сжатие deflate и свой
алфавит base64. Расшифровав его, можно сравнить с `.puml` — картинка в README
не устареет незаметно.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import zlib
from pathlib import Path

from leasing import DOCS_DIR

DIAGRAMS = DOCS_DIR / "diagrams"
_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-_"
_SRC = re.compile(r"<\?plantuml-src (\S+)\?>")
_TRANSITION = re.compile(r"^\s*(\[\*\]|\w+)\s*-+>\s*(\[\*\]|\w+)", re.MULTILINE)


def decode(encoded: str) -> str:
    bits = [_ALPHABET.index(ch) for ch in encoded]
    data = bytearray()
    for i in range(0, len(bits), 4):
        chunk = bits[i : i + 4] + [0] * (4 - len(bits[i : i + 4]))
        n = chunk[0] << 18 | chunk[1] << 12 | chunk[2] << 6 | chunk[3]
        data += bytes(((n >> 16) & 0xFF, (n >> 8) & 0xFF, n & 0xFF))
    return zlib.decompressobj(-15).decompress(bytes(data)).decode("utf-8")


def embedded_source(svg: Path) -> str | None:
    match = _SRC.search(svg.read_text(encoding="utf-8"))
    return decode(match.group(1)) if match else None


def _normalize(text: str) -> str:
    """PlantUML вшивает текст без строк @startuml и @enduml."""
    lines = [line.rstrip() for line in text.strip().splitlines()]
    return "\n".join(line for line in lines if not line.startswith(("@startuml", "@enduml")))


def stale() -> list[str]:
    """Диаграммы без SVG или с SVG от другой версии исходника."""
    problems = []
    for puml in sorted(DIAGRAMS.glob("*.puml")):
        svg = puml.with_suffix(".svg")
        if not svg.exists():
            problems.append(f"{svg.name}: нет картинки")
            continue
        source = embedded_source(svg)
        if source is None or _normalize(source) != _normalize(puml.read_text(encoding="utf-8")):
            problems.append(f"{svg.name}: картинка отстала от {puml.name}")
    return problems


def state_transitions(puml: Path | None = None) -> set[tuple[str, str]]:
    """Переходы диаграммы состояний без начальной и конечной точек."""
    text = (puml or DIAGRAMS / "state-application.puml").read_text(encoding="utf-8")
    return {(a, b) for a, b in _TRANSITION.findall(text) if "[*]" not in (a, b)}


def render(jar: Path) -> None:
    """Перерисовать все SVG: java -jar plantuml.jar -tsvg docs/diagrams/*.puml."""
    java = shutil.which("java")
    if java is None:
        raise RuntimeError("для отрисовки нужна Java 8+")
    files = [str(p) for p in sorted(DIAGRAMS.glob("*.puml"))]
    subprocess.run([java, "-jar", str(jar), "-tsvg", "-charset", "UTF-8", *files], check=True)  # noqa: S603
