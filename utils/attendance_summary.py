"""Посещаемость по расписанию группы: отмеченные пары / все прошедшие слоты → баллы 0–100."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import date

from sqlalchemy import select

from database import Attendance, GroupSemesterSubject, Schedule
from utils.group_semesters import subject_key


async def semester_subject_keys(
    session, study_group_id: int, semester_number: int
) -> list[str]:
    rows = await session.scalars(
        select(GroupSemesterSubject.subject_key).where(
            GroupSemesterSubject.study_group_id == study_group_id,
            GroupSemesterSubject.semester_number == semester_number,
        )
    )
    return list(dict.fromkeys(str(k) for k in rows if k))


async def attendance_slots_by_subject_key(
    session,
    study_group_id: int,
    user_id: int,
    today: date,
) -> dict[str, tuple[int, int]]:
    """Для каждого subject_key: (число уникальных отмеченных пар, число слотов в расписании).

    Считаем по уникальному ``schedule_id``: несколько записей ``Attendance`` на одну пару
    (разные mark_date) не должны завышать числитель.
    """
    total_by_sk: dict[str, set[int]] = defaultdict(set)
    r1 = await session.execute(
        select(Schedule.id, Schedule.subject).where(
            Schedule.study_group_id == study_group_id,
            Schedule.lesson_date.isnot(None),
            Schedule.lesson_date <= today,
        )
    )
    for sid, subj in r1.all():
        total_by_sk[subject_key(subj)].add(int(sid))

    present_by_sk: dict[str, set[int]] = defaultdict(set)
    r2 = await session.execute(
        select(Schedule.subject, Attendance.schedule_id)
        .join(Attendance, Attendance.schedule_id == Schedule.id)
        .where(
            Attendance.user_id == user_id,
            Attendance.is_present.is_(True),
            Schedule.study_group_id == study_group_id,
            Schedule.lesson_date.isnot(None),
            Schedule.lesson_date <= today,
        )
    )
    for subj, att_sid in r2.all():
        present_by_sk[subject_key(subj)].add(int(att_sid))

    keys = set(total_by_sk) | set(present_by_sk)
    return {
        k: (
            len(present_by_sk[k]) if k in present_by_sk else 0,
            len(total_by_sk[k]) if k in total_by_sk else 0,
        )
        for k in keys
    }


def attendance_points(present: int, total: int) -> int | None:
    if total <= 0:
        return None
    return round(100 * present / total)


def aggregate_attendance(
    slots: dict[str, tuple[int, int]], subject_keys: Iterable[str]
) -> dict[str, int | None]:
    present = total = 0
    for sk in subject_keys:
        p, t = slots.get(sk, (0, 0))
        present += p
        total += t
    return {
        "present": present,
        "total": total,
        "points": attendance_points(present, total),
    }
