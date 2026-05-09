"""Семестры и предметы каталога группы (не из «живого» расписания, а из учётных записей)."""

from __future__ import annotations

from sqlalchemy import func, select

from database import Grade, GroupSemesterSubject, User


def subject_key(s: str) -> str:
    return (s or "").strip().casefold()


async def semester_numbers_for_group(session, study_group_id: int) -> list[int]:
    """Все номера семестров, по которым есть каталог предметов или баллы у членов группы."""
    nums: set[int] = set()
    q1 = await session.scalars(
        select(GroupSemesterSubject.semester_number).where(
            GroupSemesterSubject.study_group_id == study_group_id
        )
    )
    for n in q1:
        nums.add(int(n))
    q2 = await session.scalars(
        select(Grade.semester_number)
        .join(User, Grade.user_id == User.id)
        .where(User.study_group_id == study_group_id)
        .distinct()
    )
    for n in q2:
        nums.add(int(n))
    return sorted(nums)


async def subjects_for_group_semester(
    session, study_group_id: int, semester_number: int
) -> list[str]:
    q = await session.scalars(
        select(GroupSemesterSubject.subject).where(
            GroupSemesterSubject.study_group_id == study_group_id,
            GroupSemesterSubject.semester_number == semester_number,
        )
    )
    by_cf: dict[str, str] = {}
    for raw in q:
        s = (raw or "").strip()
        if not s:
            continue
        k = s.casefold()
        if k not in by_cf:
            by_cf[k] = s
    return sorted(by_cf.values(), key=lambda x: x.casefold())
