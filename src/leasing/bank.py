"""Эмулятор SOAP-сервиса скоринга банка-партнёра (WSGI, lxml).

Сервер написан без SOAP-фреймворка намеренно: так видно всё, что делает сервис
по контракту, — разбор конверта, проверку тела по XSD, ответ и SOAP Fault.

Входной XML — чужие данные: разбор без DTD, внешних сущностей и сети (NFR-02),
размер тела ограничен.
"""

from __future__ import annotations

import copy
import re
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from socketserver import ThreadingMixIn
from typing import Any
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from lxml import etree

from leasing import API_DIR, NS
from leasing import inn as inn_mod
from leasing.scoring import UNREGISTERED, Application, Decision, decide

SOAP_ENV = "http://schemas.xmlsoap.org/soap/envelope/"
MAX_BODY = 64 * 1024
_PARSER = etree.XMLParser(
    resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False, remove_blank_text=True
)
_SCHEMA = etree.XMLSchema(etree.parse(str(API_DIR / "scoring.xsd")))
_EXPECTED = re.compile(r"Expected is .*?\{[^}]*\}(\w+)")

StartResponse = Callable[[str, list[tuple[str, str]]], Any]


class BankFault(Exception):
    def __init__(self, code: str, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.code, self.message, self.field = code, message, field


@dataclass(slots=True)
class _Stored:
    app: Application
    decision: Decision
    submitted_at: float


@dataclass(slots=True)
class Bank:
    """Состояние банка и режим отказа для демонстраций: ok, down (503), slow (ответ после паузы)."""

    review_seconds: float = 0.0
    clock: Callable[[], float] = time.monotonic
    mode: str = "ok"
    slow_seconds: float = 0.0
    calls: list[str] = field(default_factory=list)
    _apps: dict[str, _Stored] = field(default_factory=dict)
    _by_request: dict[str, str] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def submit(self, app: Application) -> tuple[str, bool]:
        if not inn_mod.is_valid(app.inn):
            raise BankFault("VALIDATION", "контрольная цифра ИНН не сходится", "inn")
        if app.inn in UNREGISTERED:
            # Проверка, которую шлюз повторить не может: реестр есть только у банка.
            raise BankFault("VALIDATION", "ИНН не найден в ЕГРЮЛ", "inn")
        with self._lock:
            known = self._by_request.get(app.request_id)
            if known is not None:
                if self._apps[known].app != app:
                    raise BankFault("CONFLICT", "requestId уже использован для другой заявки", "requestId")
                return known, True
            bank_id = f"SC-{len(self._apps) + 1:08d}"
            self._apps[bank_id] = _Stored(app, decide(app), self.clock())
            self._by_request[app.request_id] = bank_id
            return bank_id, False

    def decision(self, bank_id: str) -> tuple[str, Decision | None]:
        stored = self._apps.get(bank_id)
        if stored is None:
            raise BankFault("NOT_FOUND", f"заявки {bank_id} нет", "applicationId")
        if self.clock() - stored.submitted_at < self.review_seconds:
            return "IN_REVIEW", None
        return stored.decision.status, stored.decision

    def count(self) -> int:
        return len(self._apps)


# ------------------------------------------------------------------ XML


def _q(name: str) -> str:
    return f"{{{NS}}}{name}"


def _envelope(payload: etree._Element) -> bytes:
    env = etree.Element(f"{{{SOAP_ENV}}}Envelope", nsmap={"soap": SOAP_ENV, "tns": NS})
    etree.SubElement(env, f"{{{SOAP_ENV}}}Body").append(payload)
    return bytes(etree.tostring(env, xml_declaration=True, encoding="UTF-8"))


def _fault(fault: BankFault, *, client: bool = True) -> bytes:
    node = etree.Element(f"{{{SOAP_ENV}}}Fault")
    etree.SubElement(node, "faultcode").text = "soap:Client" if client else "soap:Server"
    etree.SubElement(node, "faultstring").text = fault.message
    detail = etree.SubElement(etree.SubElement(node, "detail"), _q("ScoringFault"))
    etree.SubElement(detail, _q("code")).text = fault.code
    if fault.field:
        etree.SubElement(detail, _q("field")).text = fault.field
    etree.SubElement(detail, _q("message")).text = fault.message
    return _envelope(node)


def _text(node: etree._Element, name: str) -> str:
    return node.findtext(_q(name)) or ""


def _validate(payload: etree._Element) -> None:
    doc = etree.ElementTree(copy.deepcopy(payload))
    if _SCHEMA.validate(doc):
        return
    error = _SCHEMA.error_log.last_error  # type: ignore[attr-defined]  # нет в lxml-stubs
    name = (error.path or "").rsplit("/", 1)[-1].split(":")[-1].split("[")[0]
    expected = _EXPECTED.search(error.message or "")
    if expected and "Missing child" in (error.message or ""):
        name = expected.group(1)
    raise BankFault("VALIDATION", error.message or "тело не соответствует схеме", name or None)


def _submit(bank: Bank, payload: etree._Element) -> etree._Element:
    app = Application(
        request_id=_text(payload, "requestId"),
        inn=_text(payload, "inn"),
        company_name=_text(payload, "companyName"),
        asset_type=_text(payload, "assetType"),
        amount=int(_text(payload, "amount")),
        advance_percent=int(_text(payload, "advancePercent")),
        term_months=int(_text(payload, "termMonths")),
    )
    bank_id, duplicate = bank.submit(app)
    out = etree.Element(_q("SubmitApplicationResponse"))
    etree.SubElement(out, _q("applicationId")).text = bank_id
    etree.SubElement(out, _q("status")).text = "RECEIVED"
    etree.SubElement(out, _q("duplicate")).text = "true" if duplicate else "false"
    return out


def _get_decision(bank: Bank, payload: etree._Element) -> etree._Element:
    bank_id = _text(payload, "applicationId")
    status, decision = bank.decision(bank_id)
    out = etree.Element(_q("GetDecisionResponse"))
    etree.SubElement(out, _q("applicationId")).text = bank_id
    etree.SubElement(out, _q("status")).text = status
    if decision is not None:
        if decision.approved_amount is not None:
            etree.SubElement(out, _q("approvedAmount")).text = str(decision.approved_amount)
        if decision.rate_percent is not None:
            etree.SubElement(out, _q("ratePercent")).text = str(decision.rate_percent)
        if decision.reason_code is not None:
            etree.SubElement(out, _q("reasonCode")).text = decision.reason_code
    return out


_OPERATIONS = {
    _q("SubmitApplicationRequest"): ("SubmitApplication", _submit),
    _q("GetDecisionRequest"): ("GetDecision", _get_decision),
}


def handle(bank: Bank, body: bytes) -> tuple[int, bytes]:
    """Обработать SOAP-запрос: (HTTP-статус, тело ответа)."""
    try:
        root = etree.fromstring(body, _PARSER)
    except etree.XMLSyntaxError as exc:
        return 500, _fault(BankFault("VALIDATION", f"XML не разобран: {exc}"))
    if root.getroottree().docinfo.doctype:  # type: ignore[union-attr]
        return 500, _fault(BankFault("VALIDATION", "DTD в запросе запрещён"))
    body_node = root.find(f"{{{SOAP_ENV}}}Body")
    payload = body_node[0] if body_node is not None and len(body_node) else None
    if root.tag != f"{{{SOAP_ENV}}}Envelope" or payload is None:
        return 500, _fault(BankFault("VALIDATION", "ожидался SOAP-конверт с телом"))
    operation = _OPERATIONS.get(payload.tag)
    if operation is None:
        return 500, _fault(BankFault("VALIDATION", f"неизвестная операция {payload.tag}"))
    name, fn = operation
    bank.calls.append(name)
    try:
        _validate(payload)
        result = fn(bank, payload)
    except BankFault as fault:
        return 500, _fault(fault)
    return 200, _envelope(result)


# ------------------------------------------------------------------ WSGI


def make_wsgi(bank: Bank) -> Callable[[dict[str, Any], StartResponse], Iterable[bytes]]:
    wsdl = (API_DIR / "scoring.wsdl").read_bytes()
    xsd = (API_DIR / "scoring.xsd").read_bytes()

    def app(environ: dict[str, Any], start_response: StartResponse) -> Iterable[bytes]:
        method, path = environ["REQUEST_METHOD"], environ.get("PATH_INFO", "")
        xml = [("Content-Type", "text/xml; charset=utf-8")]
        if method == "GET" and path == "/scoring" and environ.get("QUERY_STRING", "").lower() == "wsdl":
            start_response("200 OK", xml)
            return [wsdl]
        if method == "GET" and path == "/scoring.xsd":
            start_response("200 OK", xml)
            return [xsd]
        if method != "POST" or path != "/scoring":
            start_response("404 Not Found", [("Content-Type", "text/plain")])
            return [b"not found"]
        if bank.mode == "down":
            start_response("503 Service Unavailable", [("Content-Type", "text/plain")])
            return [b"maintenance"]
        length = int(environ.get("CONTENT_LENGTH") or 0)
        if length > MAX_BODY:
            start_response("413 Payload Too Large", [("Content-Type", "text/plain")])
            return [b"too large"]
        status, body = handle(bank, environ["wsgi.input"].read(length))
        if bank.mode == "slow":
            time.sleep(bank.slow_seconds)  # заявка уже принята, а ответ опаздывает
        start_response("200 OK" if status == 200 else "500 Internal Server Error", xml)
        return [body]

    return app


class _Server(ThreadingMixIn, WSGIServer):
    daemon_threads = True


class _QuietHandler(WSGIRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        pass


def serve(bank: Bank, host: str = "127.0.0.1", port: int = 8070) -> tuple[WSGIServer, threading.Thread]:
    """Запустить эмулятор в фоновом потоке; port=0 — любой свободный."""
    server = make_server(host, port, make_wsgi(bank), server_class=_Server, handler_class=_QuietHandler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    return server, thread
