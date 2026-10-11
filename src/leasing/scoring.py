"""Правила скоринга банка-партнёра — как их ведёт эмулятор.

Банк вымышленный, правила — допущение, записанное в docs/srs.md (приложение А).
Шлюз их не знает и не повторяет: он только передаёт заявку и забирает решение.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from leasing import inn as inn_mod

# Демонстрационный стоп-лист: ИНН с верной контрольной цифрой, но «плохой» историей.
STOP_LIST = frozenset({inn_mod.complete("770000001"), inn_mod.complete("500000013")})
# ИНН с верной контрольной цифрой, которых нет в реестре юрлиц: это знает только банк.
UNREGISTERED = frozenset({inn_mod.complete("990000001")})
BIG_DEAL = 10_000_000
MIN_ADVANCE_FOR_BIG_DEAL = 20
MAX_EQUIPMENT_TERM = 36


@dataclass(frozen=True, slots=True)
class Application:
    request_id: str
    inn: str
    company_name: str
    asset_type: str
    amount: int
    advance_percent: int
    term_months: int


@dataclass(frozen=True, slots=True)
class Decision:
    status: str  # APPROVED | DECLINED
    approved_amount: int | None = None
    rate_percent: Decimal | None = None
    reason_code: str | None = None


def rate_for(term_months: int) -> Decimal:
    if term_months <= 24:
        return Decimal("14.5")
    if term_months <= 36:
        return Decimal("15.5")
    return Decimal("16.9")


def decide(app: Application) -> Decision:
    if app.inn in STOP_LIST:
        return Decision("DECLINED", reason_code="STOP_LIST")
    if app.amount > BIG_DEAL and app.advance_percent < MIN_ADVANCE_FOR_BIG_DEAL:
        return Decision("DECLINED", reason_code="ADVANCE_TOO_LOW")
    if app.asset_type == "EQUIPMENT" and app.term_months > MAX_EQUIPMENT_TERM:
        return Decision("DECLINED", reason_code="TERM_TOO_LONG")
    financed = (Decimal(app.amount) * (100 - app.advance_percent) / 100).quantize(Decimal(1), ROUND_HALF_UP)
    return Decision("APPROVED", approved_amount=int(financed), rate_percent=rate_for(app.term_months))
