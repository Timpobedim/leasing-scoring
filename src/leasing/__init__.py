"""Шлюз заявок на лизинг и эмулятор SOAP-скоринга банка."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API_DIR = ROOT / "api"
DOCS_DIR = ROOT / "docs"
SQL_DIR = ROOT / "sql"
NS = "http://example.org/scoring/v1"
