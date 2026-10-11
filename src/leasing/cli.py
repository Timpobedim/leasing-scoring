"""Команды: эмулятор банка, шлюз, повтор отправки, отчёты, трассировка, демонстрация."""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path
from typing import Any

from leasing import DOCS_DIR, diagrams, reports, scoring, trace
from leasing import bank as bank_mod
from leasing import inn as inn_mod
from leasing.client import ScoringClient
from leasing.gateway import Gateway, create_app
from leasing.store import Store

DEFAULT_BANK = "http://127.0.0.1:8070/scoring"


def cmd_bank(args: argparse.Namespace) -> int:
    server, thread = bank_mod.serve(bank_mod.Bank(review_seconds=args.review_seconds), port=args.port)
    print(f"эмулятор банка: http://127.0.0.1:{server.server_port}/scoring?wsdl — Ctrl+C для выхода")
    try:
        thread.join()
    except KeyboardInterrupt:
        server.shutdown()
    return 0


def cmd_api(args: argparse.Namespace) -> int:
    import uvicorn

    gateway = Gateway(Store(args.db), ScoringClient(args.bank))
    uvicorn.run(create_app(gateway), host="127.0.0.1", port=args.port)
    return 0


def cmd_retry(args: argparse.Namespace) -> int:
    rows = Gateway(Store(args.db), ScoringClient(args.bank)).retry_pending()
    for row in rows:
        print(f"{row['id']}: {row['status']} (попыток {row['attempts']})")
    print(f"повторено: {len(rows)}")
    return 0


def _print_rows(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("  (пусто)")
    for row in rows:
        print("  " + ", ".join(f"{k}={v}" for k, v in row.items()))


def cmd_report(args: argparse.Namespace) -> int:
    store = Store(args.db)
    for name in reports.queries():
        print(f"{name}:")
        _print_rows(reports.run(store.conn, name))
    return 0


def cmd_trace(args: argparse.Namespace) -> int:
    matrix = trace.build()
    text = trace.render(matrix)
    target = DOCS_DIR / "traceability.md"
    if args.check:
        fresh = target.exists() and target.read_text(encoding="utf-8") == text
        print("матрица актуальна" if fresh else "матрица устарела: leasing trace")
        return 0 if fresh and not matrix.untested() else 1
    target.write_text(text, encoding="utf-8")
    print(
        f"{target.relative_to(DOCS_DIR.parent)}: требований {len(matrix.titles)}, "
        f"без тестов {len(matrix.untested())}, неизвестных ссылок {len(matrix.unknown())}"
    )
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    diagrams.render(Path(args.jar))
    problems = diagrams.stale()
    print("\n".join(problems) or "все диаграммы отрисованы")
    return 1 if problems else 0


def cmd_demo(_: argparse.Namespace) -> int:
    import warnings

    with warnings.catch_warnings():  # Starlette предупреждает о будущей замене httpx — демонстрации это не касается
        warnings.simplefilter("ignore")
        from fastapi.testclient import TestClient

    bank = bank_mod.Bank()
    server, _thread = bank_mod.serve(bank, port=0)
    address = f"http://127.0.0.1:{server.server_port}/scoring"
    store = Store()
    gateway = Gateway(store, ScoringClient(address, timeout=0.5))
    http = TestClient(create_app(gateway))

    def post(title: str, key: str | None = None, **changes: Any) -> dict[str, Any]:
        body = {
            "inn": inn_mod.complete("771234567"),
            "companyName": "ООО «Ромашка»",
            "assetType": "CAR",
            "amount": 3_000_000,
            "advancePercent": 20,
            "termMonths": 36,
            **changes,
        }
        response = http.post("/api/v1/applications", json=body, headers={"Idempotency-Key": key or str(uuid.uuid4())})
        data: dict[str, Any] = response.json()
        print(f"\n{title}\n  POST → {response.status_code} {data}")
        return data

    def get(app_id: str) -> None:
        response = http.get(f"/api/v1/applications/{app_id}")
        print(f"  GET  → {response.status_code} {response.json()}")

    ok = post("1. Обычная заявка: банк одобряет")
    get(ok["id"])
    declined = post("2. ИНН из стоп-листа банка: отказ", inn=sorted(scoring.STOP_LIST)[0])
    get(declined["id"])
    post("3. Ошибка в ИНН ловит шлюз — банк не вызывается", inn="7712345670")
    post("4. ИНН верный, но его нет в ЕГРЮЛ — это знает только банк", inn=sorted(scoring.UNREGISTERED)[0])
    key = str(uuid.uuid4())
    first = post("5. Повтор запроса с тем же ключом — та же заявка", key=key, assetType="TRUCK")
    again = post("   …повтор", key=key, assetType="TRUCK")
    print(f"  тот же номер: {first['id'] == again['id']}")
    get(first["id"])
    post("6. Тот же ключ, другие данные — конфликт", key=key, assetType="TRUCK", amount=4_000_000)

    bank.mode = "down"
    down = post("7. Банк на обслуживании: заявка ждёт повтора", amount=5_000_000)
    bank.mode = "ok"
    gateway.retry_pending()
    get(down["id"])

    bank.mode, bank.slow_seconds = "slow", 1.0
    before = bank.count()
    slow = post("8. Банк принял заявку, но ответ опоздал на таймаут", amount=6_000_000)
    bank.mode = "ok"
    gateway.retry_pending()
    get(slow["id"])
    print(f"  заявок в банке прибавилось: {bank.count() - before} — дубля нет")

    print("\nИстория заявки 7:", " → ".join(str(to) for _, to in store.history(down["id"])))
    print("\nОтчёты:")
    for name in ("approval_by_asset", "decline_reasons"):
        print(f"{name}:")
        _print_rows(reports.run(store.conn, name))
    server.shutdown()
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="leasing", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("bank", help="эмулятор SOAP-скоринга банка")
    p.add_argument("--port", type=int, default=8070)
    p.add_argument("--review-seconds", type=float, default=0.0, help="сколько банк «думает»")
    p.set_defaults(fn=cmd_bank)

    for name, fn, help_ in (
        ("api", cmd_api, "REST-шлюз"),
        ("retry", cmd_retry, "повторить WAITING_RETRY"),
        ("report", cmd_report, "SQL-отчёты"),
    ):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--db", default="leasing.db")
        if name != "report":
            p.add_argument("--bank", default=DEFAULT_BANK)
        if name == "api":
            p.add_argument("--port", type=int, default=8000)
        p.set_defaults(fn=fn)

    p = sub.add_parser("trace", help="матрица трассировки требований")
    p.add_argument("--check", action="store_true", help="только проверить, что файл актуален")
    p.set_defaults(fn=cmd_trace)

    p = sub.add_parser("render", help="перерисовать SVG диаграмм")
    p.add_argument("--jar", required=True, help="путь к plantuml.jar")
    p.set_defaults(fn=cmd_render)

    sub.add_parser("demo", help="восемь сценариев на эмуляторе").set_defaults(fn=cmd_demo)

    args = parser.parse_args(argv)
    result: int = args.fn(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
