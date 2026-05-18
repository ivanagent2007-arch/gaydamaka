"""Тесты выхода из группы и передачи роли старосты."""

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database import Base, StudyGroup, User, UserRole
from utils.group_roster import transfer_elder_role, user_leave_group


def _engine_maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    return engine, maker


async def _setup_group_with_three_users(maker) -> tuple[int, int, int, int]:
    """Возвращает (sg.id, elder.id, deputy.id, student.id)."""
    async with maker() as s:
        sg = StudyGroup(name="Г-1", join_code="ABCD1234")
        s.add(sg)
        await s.flush()
        elder = User(
            telegram_id=1, full_name="Староста", role=UserRole.elder,
            study_group_id=sg.id, group_name=sg.name,
        )
        deputy = User(
            telegram_id=2, full_name="Зам", role=UserRole.deputy_elder,
            study_group_id=sg.id, group_name=sg.name,
        )
        student = User(
            telegram_id=3, full_name="Студент", role=UserRole.student,
            study_group_id=sg.id, group_name=sg.name,
        )
        s.add_all([elder, deputy, student])
        await s.commit()
        return sg.id, elder.id, deputy.id, student.id


def test_elder_cannot_leave_while_others_are_in_group():
    async def main() -> None:
        engine, maker = _engine_maker()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sg_id, eid, _, _ = await _setup_group_with_three_users(maker)

        async with maker() as s:
            elder = await s.get(User, eid)
            ok, msg = await user_leave_group(s, elder)
            assert ok is False
            assert "передай" in msg.lower()
            assert elder.study_group_id == sg_id  # не выпустили

    asyncio.run(main())


def test_student_can_leave_freely():
    async def main() -> None:
        engine, maker = _engine_maker()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        _, _, _, st_id = await _setup_group_with_three_users(maker)

        async with maker() as s:
            st = await s.get(User, st_id)
            ok, _ = await user_leave_group(s, st)
            assert ok is True
            assert st.study_group_id is None
            assert st.role == UserRole.student
            await s.commit()

    asyncio.run(main())


def test_deputy_leave_drops_role_to_student():
    async def main() -> None:
        engine, maker = _engine_maker()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        _, _, dep_id, _ = await _setup_group_with_three_users(maker)

        async with maker() as s:
            dep = await s.get(User, dep_id)
            ok, _ = await user_leave_group(s, dep)
            assert ok is True
            assert dep.study_group_id is None
            # Должен сброситься с зама на студента, иначе при вступлении в другую
            # группу остался бы «замом без основания».
            assert dep.role == UserRole.student
            await s.commit()

    asyncio.run(main())


def test_elder_can_leave_when_alone():
    """Староста-одиночка может выйти — группа де-факто распадается."""
    async def main() -> None:
        engine, maker = _engine_maker()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with maker() as s:
            sg = StudyGroup(name="Solo", join_code="SOLO0001")
            s.add(sg)
            await s.flush()
            e = User(
                telegram_id=99, full_name="Один", role=UserRole.elder,
                study_group_id=sg.id, group_name=sg.name,
            )
            s.add(e)
            await s.commit()
            eid = e.id

        async with maker() as s:
            e = await s.get(User, eid)
            ok, _ = await user_leave_group(s, e)
            assert ok is True
            assert e.study_group_id is None
            assert e.role == UserRole.student
            await s.commit()

    asyncio.run(main())


def test_transfer_elder_role_swaps_roles():
    async def main() -> None:
        engine, maker = _engine_maker()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        _, eid, _, st_id = await _setup_group_with_three_users(maker)

        async with maker() as s:
            elder = await s.get(User, eid)
            ok, _ = await transfer_elder_role(s, elder, st_id)
            assert ok is True
            await s.commit()

        async with maker() as s:
            elder = await s.get(User, eid)
            new_elder = await s.get(User, st_id)
            assert elder.role == UserRole.student
            assert new_elder.role == UserRole.elder

    asyncio.run(main())


def test_transfer_elder_then_old_elder_can_leave():
    """После передачи бывший староста становится студентом и может выйти."""
    async def main() -> None:
        engine, maker = _engine_maker()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        _, eid, _, st_id = await _setup_group_with_three_users(maker)

        async with maker() as s:
            old_elder = await s.get(User, eid)
            ok, _ = await transfer_elder_role(s, old_elder, st_id)
            assert ok is True
            ok, _ = await user_leave_group(s, old_elder)
            assert ok is True
            assert old_elder.study_group_id is None
            await s.commit()

    asyncio.run(main())


def test_transfer_elder_self_rejected():
    async def main() -> None:
        engine, maker = _engine_maker()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        _, eid, _, _ = await _setup_group_with_three_users(maker)

        async with maker() as s:
            elder = await s.get(User, eid)
            ok, msg = await transfer_elder_role(s, elder, elder.id)
            assert ok is False
            assert "самому себе" in msg

    asyncio.run(main())


def test_transfer_elder_to_outsider_rejected():
    """Нельзя передать роль студенту из другой группы."""
    async def main() -> None:
        engine, maker = _engine_maker()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        _, eid, _, _ = await _setup_group_with_three_users(maker)

        async with maker() as s:
            other_sg = StudyGroup(name="OtherG", join_code="OTHR1111")
            s.add(other_sg)
            await s.flush()
            outsider = User(
                telegram_id=500, full_name="Чужой", role=UserRole.student,
                study_group_id=other_sg.id, group_name=other_sg.name,
            )
            s.add(outsider)
            await s.commit()
            out_id = outsider.id

        async with maker() as s:
            elder = await s.get(User, eid)
            ok, msg = await transfer_elder_role(s, elder, out_id)
            assert ok is False
            assert "не в твоей группе" in msg

    asyncio.run(main())


def test_non_elder_cannot_transfer_role():
    async def main() -> None:
        engine, maker = _engine_maker()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        _, _, dep_id, st_id = await _setup_group_with_three_users(maker)

        async with maker() as s:
            dep = await s.get(User, dep_id)
            ok, msg = await transfer_elder_role(s, dep, st_id)
            assert ok is False
            assert "староста" in msg.lower()

    asyncio.run(main())
