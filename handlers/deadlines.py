from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.state import default_state
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from zoneinfo import ZoneInfo

import config
from database import Deadline, User
from handlers.states import DeadlineStates

router = Router(name="deadlines")


def _parse_dt(s: str) -> datetime | None:
    s = s.strip()
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y %H.%M", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=ZoneInfo(config.TZ))
        except ValueError:
            continue
    return None


@router.message(F.text == "Новый дедлайн", StateFilter(default_state))
async def deadline_btn(
    message: Message, state: FSMContext, is_elder: bool, db_user: User | None
) -> None:
    if not is_elder:
        await message.answer("Только староста.")
        return
    if not db_user or not db_user.study_group_id:
        await message.answer("Сначала создай или выбери учебную группу.")
        return
    await message.answer("Заголовок дедлайна:")
    await state.set_state(DeadlineStates.title)


@router.message(Command("set_deadline"))
async def cmd_set_deadline(
    message: Message, state: FSMContext, is_elder: bool, db_user: User | None
) -> None:
    if not is_elder:
        await message.answer("Только староста.")
        return
    if not db_user or not db_user.study_group_id:
        await message.answer("Сначала создай или выбери учебную группу.")
        return
    await message.answer("Заголовок дедлайна:")
    await state.set_state(DeadlineStates.title)


@router.message(DeadlineStates.title)
async def dl_title(message: Message, state: FSMContext) -> None:
    await state.update_data(title=message.text.strip())
    await message.answer("Описание (или «-» если пусто):")
    await state.set_state(DeadlineStates.description)


@router.message(DeadlineStates.description)
async def dl_desc(message: Message, state: FSMContext) -> None:
    t = message.text.strip()
    await state.update_data(description="" if t == "-" else t)
    await message.answer("Дата и время (МСК): ДД.ММ.ГГГГ ЧЧ:ММ")
    await state.set_state(DeadlineStates.when)


@router.message(DeadlineStates.when)
async def dl_when(message: Message, state: FSMContext, session) -> None:
    dt = _parse_dt(message.text)
    if not dt:
        await message.answer("Не разобрал дату. Формат: 25.12.2026 18:00")
        return
    await state.update_data(deadline_date=dt)
    await message.answer("Предмет (или «-» пропустить):")
    await state.set_state(DeadlineStates.subject)


@router.message(DeadlineStates.subject)
async def dl_subject(message: Message, state: FSMContext, session, db_user: User | None) -> None:
    subj = message.text.strip()
    if subj == "-":
        subj = None
    data = await state.get_data()
    creator_id = db_user.id if db_user else None
    raw_dt = data["deadline_date"]
    naive_dt = raw_dt.replace(tzinfo=None) if raw_dt.tzinfo else raw_dt
    if not db_user or not db_user.study_group_id:
        await message.answer("Потеряна привязка к группе. Начни снова: /set_deadline")
        await state.clear()
        return
    session.add(
        Deadline(
            study_group_id=db_user.study_group_id,
            title=data["title"],
            description=data.get("description") or "",
            deadline_date=naive_dt,
            subject=subj,
            created_by=creator_id,
            notified_24h=False,
        )
    )
    await message.answer("Дедлайн сохранён.")
    await state.clear()
