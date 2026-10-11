from __future__ import annotations

import pytest

from leasing import cli


def test_demo_runs_all_scenarios(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["demo"]) == 0
    out = capsys.readouterr().out
    assert "тот же номер: True" in out
    assert "заявок в банке прибавилось: 1 — дубля нет" in out
    assert "NEW → WAITING_RETRY → SENT → APPROVED" in out
    assert out.count("POST → 422") == 2
    assert "POST → 409" in out


def test_trace_check_passes(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["trace", "--check"]) == 0
    assert "актуальна" in capsys.readouterr().out


def test_report_on_empty_db(tmp_path: object, capsys: pytest.CaptureFixture[str]) -> None:
    from pathlib import Path

    assert isinstance(tmp_path, Path)
    assert cli.main(["report", "--db", str(tmp_path / "x.db")]) == 0
    assert "approval_by_asset:" in capsys.readouterr().out
