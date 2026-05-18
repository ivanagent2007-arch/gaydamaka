import html
from datetime import date

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import default_state
from aiogram.types import Message
from sqlalchemy import select

from database import User
from handlers.common import cmd_menu, send_main_welcome
from handlers.states import ProfileBirthdayStates
from utils.birthday_helpers import (
    days_until_birthday,
    format_birthday_display,
    next_birthday,
    parse_birthday_text,
)

router = Router(name="birthdays")


@router.message(ProfileBirthdayStates.waiting_birthday, F.text == "Меню")
async def birthday_menu_escape(
    message: Message,
    state: FSMContext,
    is_elder: bool,
    db_user: User | None,
) -> None:
    """Иначе «Меню» перехватывается общим birthday_entered (как текст даты)."""
    await cmd_menu(message, is_elder, db_user, state)


@router.message(
    ProfileBirthdayStates.waiting_birthday,
    F.text & ~F.text.startswith("/"),
)
async def birthday_entered(message: Message, session, state: FSMContext, db_user: User | None) -> None:
    if not db_user:
        await state.clear()
        await message.answer("Сначала /start")
        return
    parsed = parse_birthday_text(message.text or "")
    if not parsed:
        await message.answer(
            "Не понял дату. Пример: <code>15.03</code> или <code>15.03.2002</code>",
            parse_mode="HTML",
        )
        return
    month, day, year = parsed
    db_user.birthday_month = month
    db_user.birthday_day = day
    db_user.birth_year = year
    # commit, а не flush: send_main_welcome ниже зовёт state.clear(), а FSM-сессии
    # нужна свободная write-lock — иначе self-deadlock на SQLite.
    await session.commit()
    await message.answer("Записал.")
    db_user = await session.get(User, db_user.id)
    await send_main_welcome(message, session, state, db_user)


@router.message(ProfileBirthdayStates.waiting_birthday, ~F.text)
async def birthday_need_text(message: Message) -> None:
    await message.answer(
        "Пришли день рождения обычным сообщением с текстом "
        "(формат <b>ДД.ММ</b> или <b>ДД.ММ.ГГГГ</b>).",
        parse_mode="HTML",
    )


@router.message(Command("birthdays"))
@router.message(F.text == "Дни рождения", StateFilter(default_state))
async def cmd_birthdays_menu(
    message: Message, session, db_user: User | None, state: FSMContext
) -> None:
    await state.clear()
    if not db_user or not db_user.study_group_id:
        await message.answer("Сначала вступи в группу: /groups")
        return

    today = date.today()
    q = await session.scalars(
        select(User).where(
            User.study_group_id == db_user.study_group_id,
            User.birthday_month.isnot(None),
            User.birthday_day.isnot(None),
        )
    )
    people = list(q)
    if not people:
        await message.answer(
            "В группе пока никто не указал день рождения. "
            "Это можно сделать при /start (формат ДД.ММ)."
        )
        return

    def sort_key(u: User) -> tuple:
        assert u.birthday_month and u.birthday_day
        n = next_birthday(u.birthday_month, u.birthday_day, today)
        return (n - today).days, u.full_name.lower()

    people.sort(key=sort_key)
    lines = ["<b>Дни рождения</b> (по ближайшей дате):"]
    for u in people:
        assert u.birthday_month and u.birthday_day
        ds = format_birthday_display(u.birthday_day, u.birthday_month, u.birth_year)
        dleft = days_until_birthday(u.birthday_month, u.birthday_day, today)
        if dleft == 0:
            left_ru = "сегодня"
        elif dleft == 1:
            left_ru = "завтра"
        else:
            left_ru = f"через {dleft} дн."
        lines.append(
            f"• {html.escape(u.full_name)} — {html.escape(ds)} — <i>{left_ru}</i>"
        )
    await message.answer("\n".join(lines), parse_mode="HTML")
