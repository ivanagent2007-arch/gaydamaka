"""Синхронизация каталога предметов по семестрам после обновления расписания."""

from __future__ import annotations

from sqlalchemy import func, select

from database import GroupSemesterSubject, StudyGroup

from .group_semesters import subject_key


async def sync_group_semester_catalog(
    session, sg: StudyGroup, subjects_now: list[str] | None = None
) -> None:
    """
    Предметы из актуального расписания (будущие даты):
    - первый раз — все записываются в текущий semester_number группы;
    - если появляются предметы, которых ещё не было ни в одном семестре группы —
      номер семестра группы увеличивается на 1, новые предметы пишутся в новый семестр.
    """
    if subjects_now is None:
        subjects_now = await distinct_schedule_subjects(session, sg.id)

    n_existing = await session.scalar(
        select(func.count())
        .select_from(GroupSemesterSubject)
        .where(GroupSemesterSubject.study_group_id == sg.id)
    )
    if n_existing == 0:
        for s in subjects_now:
            sk = subject_key(s)
            if not sk:
                continue
            session.add(
                GroupSemesterSubject(
                    study_group_id=sg.id,
                    semester_number=sg.semester_number,
                    subject=s.strip(),
                    subject_key=sk,
                )
            )
        return

    recorded_keys: set[str] = set()
    q = await session.scalars(
        select(GroupSemesterSubject.subject_key).where(
            GroupSemesterSubject.study_group_id == sg.id
        )
    )
    for row in q:
        if row:
            recorded_keys.add(row)

    new_subjects: list[str] = []
    seen_new: set[str] = set()
    for s in subjects_now:
        sk = subject_key(s)
        if not sk or sk in seen_new:
            continue
        if sk not in recorded_keys:
            new_subjects.append(s.strip())
            seen_new.add(sk)

    if not new_subjects:
        return

    sg.semester_number += 1
    for s in new_subjects:
        sk = subject_key(s)
        session.add(
            GroupSemesterSubject(
                study_group_id=sg.id,
                semester_number=sg.semester_number,
                subject=s,
                subject_key=sk,
            )
        )
