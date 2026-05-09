"""Состав группы и исключение студентов (староста)."""

from __future__ import annotations

import html
from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.state import default_state
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from database import User, UserRole
from keyboards.reply import main_menu_kb
from utils.group_context import get_study_group
from utils.group_roster import elder_remove_student, get_group_member_rows
from utils.user_roles import effective_can_kick_members

router = Router(name="group_members")

_MAX_KICK_ROWS = 24


def _esc(s: str) -> str:
    return html.escape(s or "", quote=False)


def _role_ru(u: User) -> str:
    if u.role == UserRole.elder:
        return "староста"
    if u.role == UserRole.deputy_elder:
        return "зам. старосты"
    return "студент"


def _format_roster_text(sg_name: str, members: list[User]) -> str:
    lines = [f"<b>Состав группы «{_esc(sg_name)}»</b> ({len(members)} чел.)\n"]
    for i, u in enumerate(members, start=1):
        lines.append(f"{i}. {_esc(u.full_name)} — {_role_ru(u)}")
    return "\n".join(lines)


def _kick_keyboard(members: list[User], actor_id: int) -> InlineKeyboardMarkup | None:
    removable = [u for u in members if u.id != actor_id and u.role != UserRole.elder]
    rows: list[list[InlineKeyboardButton]] = []
    for u in removable[:_MAX_KICK_ROWS]:
        label = ("Искл.: " + (u.full_name or "—"))[:60]
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"rkq:{u.id}",
                )
            ]
        )
    if not rows:
        return None
    if len(removable) > _MAX_KICK_ROWS:
        rows.append(
            [
                InlineKeyboardButton(
                    text="… остальных — в мини-приложении",
                    callback_data="rw",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("group_members"))
@router.message(F.text == "Состав группы", StateFilter(default_state))
async def cmd_group_members(
    message: Message, session, db_user: User | None, is_elder: bool, can_kick_members: bool
) -> None:
    if not db_user or not db_user.study_group_id:
        await message.answer("Сначала вступи в группу: /groups")
        return
    sg, members = await get_group_member_rows(session, db_user.study_group_id)
    if not sg:
        await message.answer("Группа не найдена.")
        return
    text = _format_roster_text(sg.name, members)
    if can_kick_members:
        text += (
            "\n\n<i>Чтобы исключить студента, нажми кнопку ниже или открой "
            "мини-приложение → «Состав».</i>"
        )
    elif is_elder:
        text += "\n\n<i>Исключать может только староста; заместитель — через старосту.</i>"
    kb = _kick_keyboard(members, db_user.id) if can_kick_members else None
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "rw")
async def cb_roster_web_hint(query: CallbackQuery) -> None:
    await query.answer("Открой мини-приложение → Ещё → Состав группы", show_alert=True)


@router.callback_query(F.data.startswith("rkq:"))
async def cb_kick_question(query: CallbackQuery, session, db_user: User | None) -> None:
    if not query.from_user or not effective_can_kick_members(
        db_user, query.from_user.id, query.from_user.username
    ):
        await query.answer("Только староста", show_alert=True)
        return
    raw = (query.data or "").split(":", 1)
    if len(raw) < 2 or not raw[1].isdigit():
        await query.answer("Ошибка")
        return
    tid = int(raw[1])
    target = await session.get(User, tid)
    if not target or not db_user or target.study_group_id != db_user.study_group_id:
        await query.answer("Не найден", show_alert=True)
        return
    if target.role == UserRole.elder:
        await query.answer("Нельзя исключить старосту", show_alert=True)
        return
    name = _esc(target.full_name[:60])
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да, исключить", callback_data=f"rky:{tid}"),
                InlineKeyboardButton(text="Отмена", callback_data="rkn"),
            ]
        ]
    )
    if query.message:
        await query.message.edit_text(
            f"Исключить из группы <b>{name}</b>? Он потеряет доступ к расписанию и материалам группы.",
            parse_mode="HTML",
            reply_markup=kb,
        )
    await query.answer()


@router.callback_query(F.data == "rkn")
async def cb_kick_no(query: CallbackQuery, session, db_user: User | None) -> None:
    if not query.from_user or not effective_can_kick_members(
        db_user, query.from_user.id, query.from_user.username
    ):
        await query.answer()
        return
    if query.message and db_user and db_user.study_group_id:
        sg, members = await get_group_member_rows(session, db_user.study_group_id)
        if sg:
            text = _format_roster_text(sg.name, members)
            text += (
                "\n\n<i>Чтобы исключить студента, нажми кнопку ниже или открой "
                "мини-приложение → «Состав».</i>"
            )
            kb = _kick_keyboard(members, db_user.id)
            await query.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await query.answer("Отменено")


@router.callback_query(F.data.startswith("rky:"))
async def cb_kick_yes(query: CallbackQuery, session, db_user: User | None) -> None:
    if not query.from_user or not effective_can_kick_members(
        db_user, query.from_user.id, query.from_user.username
    ):
        await query.answer("Только староста", show_alert=True)
        return
    raw = (query.data or "").split(":", 1)
    if len(raw) < 2 or not raw[1].isdigit():
        await query.answer("Ошибка")
        return
    tid = int(raw[1])
    ok, err = await elder_remove_student(session, db_user, tid)
    if not ok:
        await query.answer(err, show_alert=True)
        return
    await session.commit()
    await query.answer("Исключён")
    if query.message and db_user.study_group_id:
        sg, members = await get_group_member_rows(session, db_user.study_group_id)
        if sg:
            text = _format_roster_text(sg.name, members)
            text += (
                "\n\n<i>Чтобы исключить студента, нажми кнопку ниже или открой "
                "мини-приложение → «Состав».</i>"
            )
            kb = _kick_keyboard(members, db_user.id)
            await query.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
