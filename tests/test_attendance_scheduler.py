"""Тесты уведомления старост о посещаемости (без ежедневной рассылки по cron)."""

import asyncio
from datetime import date
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database import Attendance, Base, Schedule, StudyGroup, User, UserRole
from utils.scheduler import notify_elders_if_attendance_complete


def test_notify_elder_only_when_all_students_marked_all_slots() -> None:
    """
    Если в расписании на день несколько строк одного предмета в одно время
    (разные преподаватели / мини-группы), в отчёте это одна пара.
    Уведомление уходит только когда студент отметился по всем уникальным парам дня.
    """

    async def main() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

        today_d = date(2026, 4, 12)
        sent: list[str] = []

        async def capture_send(chat_id: int, text: str, **_kw) -> None:
            sent.append(text)

        bot = MagicMock()
        bot.send_message = AsyncMock(side_effect=capture_send)

        math_id: int
        async with maker() as s:
            g = StudyGroup(name="Тест-группа", join_code="testcode1")
            s.add(g)
            await s.flush()

            elder = User(
                telegram_id=900001,
                full_name="Староста",
                role=UserRole.elder,
                study_group_id=g.id,
            )
            student = User(
                telegram_id=900002,
                full_name="Иванов И.",
                role=UserRole.student,
                study_group_id=g.id,
            )
            s.add_all([elder, student])
            await s.flush()

            math = Schedule(
                study_group_id=g.id,
                group_name="",
                lesson_date=today_d,
                day_of_week=today_d.weekday(),
                lesson_number=1,
                subject="Математика",
                teacher="Препод А",
                room="101",
                start_time="09:00",
                end_time="10:30",
                lesson_kind="",
            )
            s.add(math)
            await s.flush()
            math_id = math.id

            english_ids: list[int] = []
            for t_name in ("Препод B", "Препод C", "Препод D", "Препод E"):
                les = Schedule(
                    study_group_id=g.id,
                    group_name="",
                    lesson_date=today_d,
                    day_of_week=today_d.weekday(),
                    lesson_number=2,
                    subject="Английский язык",
                    teacher=t_name,
                    room="202",
                    start_time="11:00",
                    end_time="12:30",
                    lesson_kind="",
                )
                s.add(les)
                await s.flush()
                english_ids.append(les.id)

            s.add(
                Attendance(
                    user_id=student.id,
                    schedule_id=english_ids[0],
                    mark_date=today_d,
                    is_present=True,
                )
            )
            await s.commit()

        async with maker() as session:
            g_row = (await session.scalars(select(StudyGroup))).first()
            assert g_row is not None
            await notify_elders_if_attendance_complete(bot, session, g_row, today_d)

        assert sent == []

        async with maker() as session:
            stu = (
                await session.scalars(select(User).where(User.role == UserRole.student))
            ).first()
            g_row = (await session.scalars(select(StudyGroup))).first()
            assert stu is not None and g_row is not None
            session.add(
                Attendance(
                    user_id=stu.id,
                    schedule_id=math_id,
                    mark_date=today_d,
                    is_present=True,
                )
            )
            await session.flush()
            await notify_elders_if_attendance_complete(bot, session, g_row, today_d)
            await session.commit()

        assert len(sent) == 1
        report = sent[0]
        assert "(пар в расписании: 2):" in report
        assert "Иванов И.: посещено 2 из 2 пар" in report

    asyncio.run(main())
