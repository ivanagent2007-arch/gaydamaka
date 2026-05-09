import random
from datetime import datetime

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import select

from database import SantaGame, SantaPair, User, UserRole
from utils.group_context import get_study_group

router = Router(name="santa")


def _derangement_indices(n: int) -> list[int]:
    order = list(range(n))
    for _ in range(500):
        random.shuffle(order)
        if all(order[i] != i for i in range(n)):
            return order
    order = list(range(n))
    order = order[1:] + order[:1]
    return order


@router.message(Command("start_santa"))
async def cmd_start_santa(
    message: Message, session, is_elder: bool, db_user: User | None
) -> None:
    if not is_elder:
        await message.answer("Только староста.")
        return
    sg = await get_study_group(session, db_user)
    if not sg:
        await message.answer("Сначала создай или выбери учебную группу.")
        return
    q = await session.scalars(
        select(User).where(
            User.study_group_id == sg.id,
            User.role == UserRole.student,
        )
    )
    students = list(q)
    if len(students) < 2:
        await message.answer("Нужно минимум 2 студента с ролью student в этой группе.")
        return
    year = datetime.now().year
    old = (
        await session.scalars(
            select(SantaGame).where(
                SantaGame.study_group_id == sg.id,
                SantaGame.year == year,
            )
        )
    ).all()
    for g in old:
        g.is_active = False
    game = SantaGame(
        study_group_id=sg.id,
        group_name=sg.name,
        year=year,
        is_active=True,
    )
    session.add(game)
    await session.flush()

    idx = _derangement_indices(len(students))
    for i, giver in enumerate(students):
        receiver = students[idx[i]]
        session.add(
            SantaPair(game_id=game.id, giver_id=giver.id, receiver_id=receiver.id)
        )
    await message.answer(
        f"Тайный Санта {year} запущен для {len(students)} участников.\n"
        "Студенты увидят получателя в мини-приложении."
    )
