"""Жизненный цикл заявки в шлюзе.

Таблица переходов — единственный источник правды: диаграмма состояний
docs/diagrams/state-application.puml сверяется с ней тестом.
"""

from enum import StrEnum


class Status(StrEnum):
    NEW = "NEW"
    SENT = "SENT"
    APPROVED = "APPROVED"
    DECLINED = "DECLINED"
    INVALID = "INVALID"
    WAITING_RETRY = "WAITING_RETRY"
    FAILED = "FAILED"


S = Status
TRANSITIONS: frozenset[tuple[Status, Status]] = frozenset(
    {
        (S.NEW, S.SENT),  # банк принял заявку
        (S.NEW, S.INVALID),  # банк отверг поле
        (S.NEW, S.WAITING_RETRY),  # банк недоступен или не ответил вовремя
        (S.WAITING_RETRY, S.SENT),
        (S.WAITING_RETRY, S.INVALID),
        (S.WAITING_RETRY, S.WAITING_RETRY),  # повтор тоже не удался
        (S.WAITING_RETRY, S.FAILED),  # попытки кончились
        (S.SENT, S.APPROVED),
        (S.SENT, S.DECLINED),
    }
)
FINAL = frozenset({S.APPROVED, S.DECLINED, S.INVALID, S.FAILED})


class IllegalTransition(Exception):
    pass


def check(current: Status, new: Status) -> None:
    if (current, new) not in TRANSITIONS:
        raise IllegalTransition(f"{current} → {new} не предусмотрен жизненным циклом заявки")
