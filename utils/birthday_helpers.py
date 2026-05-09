"""Разбор и расчёт дат дней рождения (без года — ежегодное повторение)."""

from __future__ import annotations

import calendar
from datetime import date, datetime


def parse_birthday_text(text: str) -> tuple[int, int, int | None] | None:
    """ДД.ММ, ДД.ММ.ГГГГ или ДД.ММ.ГГ. Возвращает (месяц, день, год или None)."""
    raw = (text or "").strip()
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%d.%m"):
        try:
            parsed = datetime.strptime(raw, fmt).date()
            if fmt == "%d.%m":
                return parsed.month, parsed.day, None
            return parsed.month, parsed.day, parsed.year
        except ValueError:
            continue
    return None


def _safe_birth_date(y: int, month: int, day: int) -> date:
    if month == 2 and day == 29 and not calendar.isleap(y):
        day = 28
    try:
        return date(y, month, day)
    except ValueError:
        return date(y, month, 28)


def next_birthday(month: int, day: int, today: date) -> date:
    """Ближайший день рождения в календаре, не раньше today."""
    for y in range(today.year, today.year + 2):
        cand = _safe_birth_date(y, month, day)
        if cand >= today:
            return cand
    return _safe_birth_date(today.year + 1, month, day)


def days_until_birthday(month: int, day: int, today: date) -> int:
    n = next_birthday(month, day, today)
    return (n - today).days


def format_birthday_display(day: int, month: int, year: int | None) -> str:
    if year:
        return f"{day:02d}.{month:02d}.{year}"
    return f"{day:02d}.{month:02d}"
