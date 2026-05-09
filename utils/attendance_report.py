"""Текст отчёта о посещаемости группы за день (староста по запросу, авто — когда все отметились)."""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import Attendance, Schedule, StudyGroup, User, UserRole


def lesson_slot_key(
    subject: str | None,
    start_time: str | None,
    contingent: str | None = None,
) -> tuple[str, str, str]:
    """Ключ пары: время + предмет + поток/подгруппа (если есть)."""
    return (
        (start_time or "").strip(),
        (subject or "").strip().lower(),
        (contingent or "").strip().lower(),
    )


async def attendance_group_day_stats(
    session: AsyncSession,
    study_group: StudyGroup,
    report_date: date,
) -> dict:
    """Сводка посещаемости группы за день (для мини-приложения и текста отчёта)."""
    students = (
        await session.scalars(
            select(User).where(
                User.study_group_id == study_group.id,
                User.role == UserRole.student,
            )
        )
    ).all()
    today_lessons = (
        await session.scalars(
            select(Schedule).where(
                Schedule.study_group_id == study_group.id,
                Schedule.lesson_date == report_date,
            )
        )
    ).all()
    unique_slots: dict[tuple[str, str, str], Schedule] = {}
    for les in today_lessons:
        k = lesson_slot_key(les.subject, les.start_time, les.contingent_label)
        if k not in unique_slots:
            unique_slots[k] = les
    ordered = sorted(unique_slots.values(), key=lambda s: s.start_time)
    y = len(ordered)
    present_rows = await session.execute(
        select(
            Attendance.user_id,
            Schedule.subject,
            Schedule.start_time,
            Schedule.contingent_label,
        )
        .join(Schedule, Schedule.id == Attendance.schedule_id)
        .where(
            Schedule.study_group_id == study_group.id,
            Schedule.lesson_date == report_date,
            Attendance.mark_date == report_date,
            Attendance.is_present.is_(True),
        )
    )
    present_slots_by_user: dict[int, set[tuple[str, str, str]]] = defaultdict(set)
    for user_id, subject, start_time, contingent in present_rows.all():
        present_slots_by_user[user_id].add(
            lesson_slot_key(subject, start_time, contingent)
        )
    students_out: list[dict] = [
        {
            "full_name": st.full_name,
            "present": len(present_slots_by_user.get(st.id, set())),
            "total": y,
        }
        for st in students
    ]
    n_st = len(students_out)
    if y > 0 and n_st:
        fully_marked = sum(1 for r in students_out if r["present"] == y)
        with_any = sum(1 for r in students_out if r["present"] > 0)
        all_fully = fully_marked == n_st
    else:
        fully_marked = 0
        with_any = 0
        all_fully = False
    return {
        "group_name": study_group.name,
        "date": report_date.isoformat(),
        "lesson_slots_count": y,
        "lessons": [
            {
                "start": les.start_time,
                "end": les.end_time,
                "subject": les.subject,
                "kind": (les.lesson_kind or "").strip() or None,
                "contingent": (les.contingent_label or "").strip() or None,
            }
            for les in ordered
        ],
        "students": students_out,
        "students_total": n_st,
        "students_fully_marked_count": fully_marked,
        "students_with_any_mark_count": with_any,
        "all_students_fully_marked": all_fully,
    }


async def build_attendance_report_text(
    session: AsyncSession,
    study_group: StudyGroup,
    report_date: date,
) -> str:
    data = await attendance_group_day_stats(session, study_group, report_date)
    if not data["students"]:
        return f"[{study_group.name}] Отчёт за {report_date:%d.%m.%Y}: нет студентов."
    y = data["lesson_slots_count"]
    lines = [
        f"[{data['group_name']}] Посещаемость за {report_date:%d.%m.%Y} (пар в расписании: {y}):"
    ]
    for row in data["students"]:
        lines.append(
            f"{row['full_name']}: посещено {row['present']} из {row['total']} пар"
        )
    return "\n".join(lines)
