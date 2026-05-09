"""Предметы для баллов — уникальные названия из будущего расписания группы."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select

from database import Schedule


async def distinct_schedule_subjects(session, study_group_id: int) -> list[str]:
    """
    Уникальные непустые предметы из расписания с lesson_date >= сегодня.
    При совпадении без учёта регистра оставляется первый вариант написания из БД.
    """
    today = date.today()
    q = await session.scalars(
        select(Schedule.subject).where(
            Schedule.study_group_id == study_group_id,
            Schedule.lesson_date >= today,
        )
    )
    by_key: dict[str, str] = {}
    for raw in q:
        s = (raw or "").strip()
        if not s:
            continue
        k = s.casefold()
        if k not in by_key:
            by_key[k] = s
    return sorted(by_key.values(), key=lambda x: x.casefold())
