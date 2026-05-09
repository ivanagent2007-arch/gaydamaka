"""Создание записи «дедлайн» при добавлении ДЗ к паре (бот или мини-приложение)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from database import Deadline, Homework, Schedule


def lesson_day_end_datetime(lesson_date: date | None) -> datetime:
    """Срок по умолчанию — конец календарного дня занятия (локальное время сервера)."""
    if lesson_date:
        return datetime.combine(lesson_date, time(23, 59, 59))
    return datetime.utcnow() + timedelta(days=7)


def deadline_for_homework(
    hw: Homework,
    sch: Schedule,
    *,
    creator_user_id: int | None,
) -> Deadline:
    """hw.id должен быть уже выдан (flush)."""
    due = lesson_day_end_datetime(sch.lesson_date)
    subj = (sch.subject or "").strip()
    title = (f"ДЗ · {subj}" if subj else "Домашнее задание")[:512]
    desc = (hw.description or "").strip()
    if len(desc) > 12000:
        desc = desc[:12000] + "…"
    return Deadline(
        study_group_id=sch.study_group_id,
        title=title,
        description=desc,
        deadline_date=due,
        subject=subj[:512] if subj else None,
        created_by=creator_user_id,
        notified_24h=False,
        homework_id=hw.id,
    )
