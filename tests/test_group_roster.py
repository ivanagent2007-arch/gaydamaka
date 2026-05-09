"""Исключение из группы: только студенты, не себя."""

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database import Base, StudyGroup, User, UserRole
from utils.group_roster import elder_remove_student, get_group_member_rows


def test_elder_cannot_kick_elder_or_self():
    async def main() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

        async with maker() as s:
            g = StudyGroup(name="Г-1", join_code="ABCD1234")
            s.add(g)
            await s.flush()
            elder = User(
                telegram_id=1,
                full_name="Староста",
                role=UserRole.elder,
                study_group_id=g.id,
                group_name=g.name,
            )
            elder2 = User(
                telegram_id=2,
                full_name="Староста2",
                role=UserRole.elder,
                study_group_id=g.id,
                group_name=g.name,
            )
            st = User(
                telegram_id=3,
                full_name="Студент",
                role=UserRole.student,
                study_group_id=g.id,
                group_name=g.name,
            )
            s.add_all([elder, elder2, st])
            await s.commit()

        async with maker() as s:
            e = await s.get(User, elder.id)
            assert e is not None
            ok, _ = await elder_remove_student(s, e, e.id)
            assert ok is False
            ok2, _ = await elder_remove_student(s, e, elder2.id)
            assert ok2 is False
            ok3, _ = await elder_remove_student(s, e, st.id)
            assert ok3 is True
            await s.commit()

        async with maker() as s:
            st2 = await s.get(User, st.id)
            assert st2 is not None
            assert st2.study_group_id is None
            sg, members = await get_group_member_rows(s, g.id)
            assert sg is not None
            assert len(members) == 2

    asyncio.run(main())


def test_deputy_cannot_kick():
    async def main() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

        async with maker() as s:
            g = StudyGroup(name="Г-2", join_code="ZZZZ9999")
            s.add(g)
            await s.flush()
            deputy = User(
                telegram_id=10,
                full_name="Зам",
                role=UserRole.deputy_elder,
                study_group_id=g.id,
                group_name=g.name,
            )
            st = User(
                telegram_id=11,
                full_name="Студент",
                role=UserRole.student,
                study_group_id=g.id,
                group_name=g.name,
            )
            s.add_all([deputy, st])
            await s.commit()

        async with maker() as s:
            d = await s.get(User, deputy.id)
            assert d is not None
            ok, msg = await elder_remove_student(s, d, st.id)
            assert ok is False
            assert "старост" in msg.lower()

    asyncio.run(main())
