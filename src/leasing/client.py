"""Клиент банковского скоринга на zeep: WSDL из репозитория, ошибки — в понятные исключения.

Шлюзу не важно, что под капотом SOAP: он получает (id в банке, повтор ли) или
решение, а отказы приходят тремя видами — поле не прошло проверку банка,
ключ уже занят другой заявкой, банк недоступен.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import requests
from lxml import etree
from zeep import Client, Settings
from zeep.exceptions import Fault, TransportError
from zeep.transports import Transport

from leasing import API_DIR, NS
from leasing.scoring import Application

TIMEOUT_SECONDS = 5.0  # NFR-01


class BankError(Exception):
    pass


class BankValidation(BankError):
    def __init__(self, field: str | None, message: str) -> None:
        super().__init__(message)
        self.field, self.message = field, message


class BankConflict(BankError):
    pass


class BankUnavailable(BankError):
    """Таймаут, отказ соединения или 5xx без SOAP Fault: заявку можно отправить ещё раз."""


@dataclass(frozen=True, slots=True)
class BankDecision:
    status: str  # IN_REVIEW | APPROVED | DECLINED
    approved_amount: int | None
    rate_percent: Decimal | None
    reason_code: str | None


def _fault_detail(fault: Fault) -> tuple[str, str | None, str]:
    detail = fault.detail
    node = detail.find(f"{{{NS}}}ScoringFault") if detail is not None else None
    if node is None:
        return "UNKNOWN", None, str(fault.message)
    return (
        node.findtext(f"{{{NS}}}code") or "UNKNOWN",
        node.findtext(f"{{{NS}}}field"),
        node.findtext(f"{{{NS}}}message") or str(fault.message),
    )


class ScoringClient:
    def __init__(
        self, address: str, *, timeout: float = TIMEOUT_SECONDS, session: requests.Session | None = None
    ) -> None:
        transport = Transport(session=session, timeout=timeout, operation_timeout=timeout)
        client = Client(
            str(API_DIR / "scoring.wsdl"), transport=transport, settings=Settings(strict=True, xml_huge_tree=False)
        )
        self._service: Any = client.create_service(f"{{{NS}}}ScoringBinding", address)

    def _call(self, operation: str, **kwargs: Any) -> Any:
        try:
            return getattr(self._service, operation)(**kwargs)
        except Fault as fault:
            code, field, message = _fault_detail(fault)
            if code == "CONFLICT":
                raise BankConflict(message) from fault
            if code == "VALIDATION":
                raise BankValidation(field, message) from fault
            raise BankError(f"{code}: {message}") from fault
        except (TransportError, requests.RequestException, etree.XMLSyntaxError) as exc:
            raise BankUnavailable(str(exc)) from exc

    def submit(self, app: Application) -> tuple[str, bool]:
        result = self._call(
            "SubmitApplication",
            requestId=app.request_id,
            inn=app.inn,
            companyName=app.company_name,
            assetType=app.asset_type,
            amount=app.amount,
            advancePercent=app.advance_percent,
            termMonths=app.term_months,
        )
        return str(result.applicationId), bool(result.duplicate)

    def decision(self, bank_id: str) -> BankDecision:
        result = self._call("GetDecision", applicationId=bank_id)
        return BankDecision(
            status=str(result.status),
            approved_amount=result.approvedAmount,
            rate_percent=result.ratePercent,
            reason_code=result.reasonCode,
        )
