"""Шлюз: REST для CRM снаружи, SOAP банка внутри, жизненный цикл заявки посередине."""

from __future__ import annotations

import sqlite3
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from pydantic.alias_generators import to_camel

from leasing import inn as inn_mod
from leasing.client import BankConflict, BankUnavailable, BankValidation, ScoringClient
from leasing.scoring import Application
from leasing.states import Status
from leasing.store import KeyConflict, NewApplication, Store

MAX_ATTEMPTS = 3
UUID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"


class ApplicationIn(BaseModel):
    """Тело POST /applications. Ограничения те же, что в XSD банка и в openapi.yaml (NFR-03)."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    inn: Annotated[str, StringConstraints(pattern=r"^[0-9]{10}$")]
    company_name: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    asset_type: Literal["CAR", "TRUCK", "EQUIPMENT"]
    amount: Annotated[int, Field(ge=100_000, le=50_000_000)]
    advance_percent: Annotated[int, Field(ge=10, le=49)]
    term_months: Annotated[int, Field(ge=12, le=60)]

    @field_validator("inn")
    @classmethod
    def _inn_checksum(cls, value: str) -> str:
        if not inn_mod.is_valid(value):
            raise ValueError("контрольная цифра ИНН не сходится")
        return value


class Gateway:
    """Поведение шлюза без HTTP: его же вызывают команда retry и демонстрация."""

    def __init__(self, store: Store, bank: ScoringClient, *, max_attempts: int = MAX_ATTEMPTS) -> None:
        self.store, self.bank, self.max_attempts = store, bank, max_attempts

    def submit(self, key: str, data: NewApplication) -> sqlite3.Row:
        row, created = self.store.create(key, data)  # FR-03: KeyConflict при других данных
        if not created and row["status"] != Status.NEW:
            return row
        return self._send(row)

    def _send(self, row: sqlite3.Row) -> sqlite3.Row:
        app_id = row["id"]
        attempts = self.store.bump_attempts(app_id)
        app = Application(
            request_id=row["idempotency_key"],
            inn=row["inn"],
            company_name=row["company_name"],
            asset_type=row["asset_type"],
            amount=row["amount"],
            advance_percent=row["advance_percent"],
            term_months=row["term_months"],
        )
        try:
            bank_id, duplicate = self.bank.submit(app)
        except BankValidation as exc:
            return self.store.move(
                app_id, Status.INVALID, note="банк отверг поле", error_field=exc.field, error_message=exc.message
            )
        except BankConflict as exc:
            return self.store.move(
                app_id, Status.INVALID, note="конфликт ключа в банке", error_field="requestId", error_message=str(exc)
            )
        except BankUnavailable as exc:
            give_up = row["status"] == Status.WAITING_RETRY and attempts >= self.max_attempts
            return self.store.move(
                app_id, Status.FAILED if give_up else Status.WAITING_RETRY, note=f"попытка {attempts}: {exc}"[:300]
            )
        note = "банк уже знал заявку по requestId — дубля нет" if duplicate else None
        return self.store.move(app_id, Status.SENT, note=note, bank_id=bank_id)

    def refresh(self, app_id: str) -> sqlite3.Row | None:
        """Статус заявки; отправленную — сверить с банком (FR-07, FR-08)."""
        row = self.store.get(app_id)
        if row is None or row["status"] != Status.SENT:
            return row
        try:
            decision = self.bank.decision(row["bank_id"])
        except BankUnavailable:
            return row  # банк ответит в следующий раз; статус не портим
        if decision.status not in ("APPROVED", "DECLINED"):
            return row
        rate = float(decision.rate_percent) if decision.rate_percent is not None else None
        return self.store.move(
            app_id,
            Status(decision.status),
            approved_amount=decision.approved_amount,
            rate_percent=rate,
            reason_code=decision.reason_code,
        )

    def retry_pending(self) -> list[sqlite3.Row]:
        """Повторить отправку всех WAITING_RETRY (FR-06). Запускается по расписанию."""
        return [self._send(row) for row in self.store.by_status(Status.WAITING_RETRY)]


def to_out(row: sqlite3.Row) -> dict[str, Any]:
    decision = None
    if row["status"] in (Status.APPROVED, Status.DECLINED):
        decision = {
            "approvedAmount": row["approved_amount"],
            "ratePercent": row["rate_percent"],
            "reasonCode": row["reason_code"],
        }
    return {
        "id": row["id"],
        "status": row["status"],
        "bankId": row["bank_id"],
        "attempts": row["attempts"],
        "decision": decision,
    }


_MESSAGES = {
    "greater_than_equal": "не меньше {ge}",
    "less_than_equal": "не больше {le}",
    "string_pattern_mismatch": "не соответствует формату {pattern}",
    "string_too_short": "не короче {min_length} символов",
    "string_too_long": "не длиннее {max_length} символов",
    "literal_error": "допустимо: {expected}",
    "missing": "обязательное поле",
    "extra_forbidden": "лишнее поле",
    "int_type": "нужно целое число",
    "int_parsing": "нужно целое число",
    "int_from_float": "нужно целое число",
}


def russian_message(error: Any) -> str:
    """Текст ошибки для менеджера: CRM показывает его как есть."""
    template = _MESSAGES.get(str(error.get("type")))
    if template is not None:
        try:
            ctx = {k: str(v).replace(" or ", ", ") for k, v in error.get("ctx", {}).items()}
            return template.format(**ctx)
        except KeyError:
            pass
    return str(error.get("msg", "неверное значение")).removeprefix("Value error, ")


def _error(
    status: int, kind: Literal["validation", "conflict", "not_found"], message: str, field: str | None = None
) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": kind, "field": field, "message": message})


def create_app(gateway: Gateway) -> FastAPI:
    app = FastAPI(title="Шлюз заявок на лизинг", version="1.0.0")

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0]
        loc = [str(part) for part in first.get("loc", ())]
        field = loc[-1] if loc else None
        if loc[:1] == ["header"]:
            field = "Idempotency-Key"
        return _error(422, "validation", russian_message(first), field)

    @app.post("/api/v1/applications", status_code=202)
    def create_application(
        body: ApplicationIn,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key", pattern=UUID_PATTERN)],
    ) -> Any:
        data = NewApplication(
            inn=body.inn,
            company_name=body.company_name,
            asset_type=body.asset_type,
            amount=body.amount,
            advance_percent=body.advance_percent,
            term_months=body.term_months,
        )
        try:
            row = gateway.submit(idempotency_key, data)
        except KeyConflict:
            return _error(409, "conflict", "Idempotency-Key уже использован для другой заявки", "Idempotency-Key")
        if row["status"] == Status.INVALID:
            return _error(422, "validation", row["error_message"] or "банк отверг заявку", row["error_field"])
        return JSONResponse(status_code=202, content=to_out(row))

    @app.get("/api/v1/applications/{app_id}")
    def get_application(app_id: str) -> Any:
        row = gateway.refresh(app_id)
        if row is None:
            return _error(404, "not_found", f"заявки {app_id} нет")
        return to_out(row)

    return app
