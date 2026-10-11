"""ИНН юридического лица: десять цифр, последняя — контрольная."""

_WEIGHTS = (2, 4, 10, 3, 5, 9, 4, 6, 8)


def check_digit(first_nine: str) -> int:
    return sum(int(d) * w for d, w in zip(first_nine, _WEIGHTS, strict=True)) % 11 % 10


def is_valid(inn: str) -> bool:
    if len(inn) != 10 or not inn.isascii() or not inn.isdigit():
        return False
    return check_digit(inn[:9]) == int(inn[9])


def complete(first_nine: str) -> str:
    """Дописать контрольную цифру: так строятся ИНН для примеров и тестов."""
    return first_nine + str(check_digit(first_nine))
