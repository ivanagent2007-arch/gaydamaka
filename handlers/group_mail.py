"""Корпоративная почта группы: ящики по отправителю, просмотр, настройка IMAP."""

from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import default_state
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, select

from database import GroupEmailMailbox, GroupEmailMessage, GroupEmailPollState, StudyGroup, User
from handlers.states import GroupMailSettingsStates, MailBoxStates
from utils.group_context import get_study_group
from utils.group_email_imap import is_valid_email
from utils.group_mail_worker import poll_one_group_mail
from utils.user_roles import effective_is_elder

router = Router(name="group_mail")


def _mail_root_kb(
    is_elder: bool,
    counts: dict[int | None, int],
    boxes: list[GroupEmailMailbox],
    imap_ready: bool,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if imap_ready:
        rows.append(
            [InlineKeyboardButton(text="🔄 Обновить почту", callback_data="gp:sync")]
        )
    n_other = counts.get(None, 0)
    rows.append(
        [
            InlineKeyboardButton(
                text=f"📥 Без ящика ({n_other})",
                callback_data="gp:b:0",
            )
        ]
    )
    for b in boxes:
        cnt = counts.get(b.id, 0)
        label = b.title[:28] + ("…" if len(b.title) > 28 else "")
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"📂 {label} ({cnt})",
                    callback_data=f"gp:b:{b.id}",
                )
            ]
        )
    if is_elder:
        rows.append([InlineKeyboardButton(text="➕ Ящик (отправитель)", callback_data="gp:add")])
        rows.append([InlineKeyboardButton(text="⚙ Почта IMAP", callback_data="gp:set")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _mailbox_counts(session, study_group_id: int) -> dict[int | None, int]:
    q = await session.execute(
        select(GroupEmailMessage.mailbox_id, func.count(GroupEmailMessage.id)).where(
            GroupEmailMessage.study_group_id == study_group_id
        ).group_by(GroupEmailMessage.mailbox_id)
    )
    return {mid: int(c) for mid, c in q.all()}


async def _send_mail_menu(message: Message, session, db_user: User | None, is_elder: bool) -> None:
    if not db_user or not db_user.study_group_id:
        await message.answer("Сначала вступи в группу.")
        return
    sg = await get_study_group(session, db_user)
    if not sg:
        await message.answer("Группа не найдена.")
        return
    counts = await _mailbox_counts(session, sg.id)
    boxes = list(
        (
            await session.scalars(
                select(GroupEmailMailbox)
                .where(GroupEmailMailbox.study_group_id == sg.id)
                .order_by(GroupEmailMailbox.id)
            )
        ).all()
    )
    imap_ready = bool(sg.corporate_email and sg.imap_password)
    lines = [
        "<b>Почта группы</b>",
        f"Ящик: <code>{html.escape(sg.corporate_email or 'не задан')}</code>",
        "",
        "Нажми «Обновить почту», чтобы сразу проверить ящик, или открой раздел ниже.",
        "Текст писем — в мини-приложении: «Мини-приложение» → «Ещё» → «Почта группы».",
    ]
    await message.answer(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=_mail_root_kb(is_elder, counts, boxes, imap_ready),
    )


@router.message(F.text == "Почта", StateFilter(default_state))
async def text_mail_menu(
    message: Message, session, db_user: User | None, is_elder: bool
) -> None:
    await _send_mail_menu(message, session, db_user, is_elder)


@router.message(Command("mail"))
async def cmd_mail(
    message: Message, session, db_user: User | None, is_elder: bool
) -> None:
    await _send_mail_menu(message, session, db_user, is_elder)


async def _run_mail_sync(message: Message, session, db_user: User | None) -> None:
    if not db_user or not db_user.study_group_id:
        await message.answer("Сначала вступи в группу.")
        return
    sg = await session.get(StudyGroup, db_user.study_group_id)
    if not sg:
        await message.answer("Группа не найдена.")
        return
    if not sg.corporate_email or not sg.imap_password:
        await message.answer("Почта группы не настроена (староста: «Почта» → «Почта IMAP»).")
        return
    n, err = await poll_one_group_mail(message.bot, session, sg)
    if err:
        await session.rollback()
        await message.answer(f"Не удалось проверить почту: {err[:800]}")
        return
    if n:
        await message.answer(f"Готово: новых писем — {n}.")
    else:
        await message.answer("Новых писем нет.")


@router.message(Command("mail_now"))
async def cmd_mail_now(message: Message, session, db_user: User | None) -> None:
    await _run_mail_sync(message, session, db_user)


@router.callback_query(F.data == "gp:sync")
async def cb_sync_mail(query: CallbackQuery, session, db_user: User | None) -> None:
    if not query.message:
        await query.answer()
        return
    await query.answer("Проверяю…")
    await _run_mail_sync(query.message, session, db_user)


@router.callback_query(F.data == "gp:add")
async def cb_add_mailbox(query: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    if not query.from_user or not effective_is_elder(db_user, query.from_user.id, query.from_user.username):
        await query.answer("Только староста", show_alert=True)
        return
    if not db_user or not db_user.study_group_id:
        await query.answer("Нет группы", show_alert=True)
        return
    await state.set_state(MailBoxStates.title)
    await state.update_data(mail_gid=db_user.study_group_id)
    if query.message:
        await query.message.answer(
            "Введи <b>название ящика</b> (как отобразится в боте), например «Деканат».",
            parse_mode="HTML",
        )
    await query.answer()


@router.message(MailBoxStates.title)
async def mailbox_title(message: Message, state: FSMContext) -> None:
    title = (message.text or "").strip()
    if len(title) < 2:
        await message.answer("Слишком коротко. Ещё раз название ящика.")
        return
    await state.update_data(mailbox_title=title)
    await state.set_state(MailBoxStates.sender_email)
    await message.answer(
        "Введи <b>e-mail отправителя</b> (точное совпадение). "
        "Письма с этого адреса попадут в этот ящик.",
        parse_mode="HTML",
    )


@router.message(MailBoxStates.sender_email)
async def mailbox_sender(
    message: Message, state: FSMContext, session, db_user: User | None
) -> None:
    raw = (message.text or "").strip().lower()
    if not is_valid_email(raw):
        await message.answer("Неверный e-mail. Пример: <code>dekanat@fa.ru</code>", parse_mode="HTML")
        return
    data = await state.get_data()
    gid = data.get("mail_gid")
    title = data.get("mailbox_title")
    if not gid or not title:
        await state.clear()
        await message.answer("Сессия сброшена. Открой «Почта» снова.")
        return

    exists = await session.scalar(
        select(GroupEmailMailbox).where(
            GroupEmailMailbox.study_group_id == gid,
            GroupEmailMailbox.sender_email == raw,
        )
    )
    if exists:
        await message.answer("Такой отправитель уже привязан к ящику.")
        return

    session.add(GroupEmailMailbox(study_group_id=gid, title=title, sender_email=raw))
    await state.clear()
    await message.answer(f"Ящик «{title}» для <code>{raw}</code> добавлен.", parse_mode="HTML")
    if db_user:
        await _send_mail_menu(message, session, db_user, True)


@router.callback_query(F.data.startswith("gp:b:"))
async def cb_mailbox_open(query: CallbackQuery, session, db_user: User | None) -> None:
    if not query.data or not query.message:
        await query.answer()
        return
    parts = query.data.split(":")
    mid = int(parts[2])
    if not db_user or not db_user.study_group_id:
        await query.answer("Нет группы", show_alert=True)
        return
    sg_id = db_user.study_group_id
    mb_id: int | None = None if mid == 0 else mid

    stmt = select(GroupEmailMessage).where(GroupEmailMessage.study_group_id == sg_id)
    if mb_id is None:
        stmt = stmt.where(GroupEmailMessage.mailbox_id.is_(None))
    else:
        stmt = stmt.where(GroupEmailMessage.mailbox_id == mb_id)
    stmt = stmt.order_by(GroupEmailMessage.received_at.desc()).limit(12)
    rows = list((await session.scalars(stmt)).all())

    if mb_id is None:
        title = "Без ящика"
    else:
        mb = await session.get(GroupEmailMailbox, mb_id)
        title = mb.title if mb else f"#{mb_id}"
    lines = [f"<b>{html.escape(title)}</b>", ""]
    if not rows:
        lines.append("Пока нет сохранённых писем.")
    for r in rows:
        lines.append(
            f"— <b>{html.escape(r.subject[:120] or '(без темы)')}</b>\n"
            f"  <i>{html.escape(r.sender[:80])}</i>\n"
            f"  <code>{r.received_at.strftime('%d.%m.%Y %H:%M')}</code>\n"
        )
        prev = (r.body_preview or "")[:600]
        if prev:
            lines.append(html.escape(prev) + "\n")
    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:3990] + "…"
    await query.message.answer(text, parse_mode="HTML")
    await query.answer()


@router.callback_query(F.data == "gp:set")
async def cb_mail_settings(
    query: CallbackQuery, state: FSMContext, session, db_user: User | None
) -> None:
    if not query.from_user or not effective_is_elder(db_user, query.from_user.id, query.from_user.username):
        await query.answer("Только староста", show_alert=True)
        return
    if not db_user or not db_user.study_group_id:
        await query.answer("Нет группы", show_alert=True)
        return
    await state.set_state(GroupMailSettingsStates.corporate_email)
    await state.update_data(mail_set_gid=db_user.study_group_id)
    sg = await get_study_group(session, db_user)
    cur = html.escape(sg.corporate_email) if sg and sg.corporate_email else "не задан"
    if query.message:
        await query.message.answer(
            f"Сейчас в боте: <code>{cur}</code>\n\n"
            "Введи <b>новый e-mail</b> ящика группы (логин IMAP):",
            parse_mode="HTML",
        )
    await query.answer()


@router.message(GroupMailSettingsStates.corporate_email)
async def mail_set_email(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip().lower()
    if not is_valid_email(raw):
        await message.answer("Неверный e-mail. Пример: <code>grupa@fa.ru</code>", parse_mode="HTML")
        return
    await state.update_data(new_mail=raw)
    await state.set_state(GroupMailSettingsStates.imap_password)
    await message.answer(
        "Введи <b>пароль приложения</b> для IMAP (хранится в базе бота).",
        parse_mode="HTML",
    )


@router.message(GroupMailSettingsStates.imap_password)
async def mail_set_password(
    message: Message, state: FSMContext, session, db_user: User | None
) -> None:
    pwd = (message.text or "").strip()
    if len(pwd) < 4:
        await message.answer("Слишком короткий пароль. Повтори.")
        return
    data = await state.get_data()
    gid = data.get("mail_set_gid")
    new_mail = data.get("new_mail")
    if not gid or not new_mail:
        await state.clear()
        await message.answer("Сессия сброшена. Открой «Почта» → «Почта IMAP».")
        return
    sg = await session.get(StudyGroup, gid)
    if not sg:
        await state.clear()
        await message.answer("Группа не найдена.")
        return
    sg.corporate_email = new_mail
    sg.imap_password = pwd
    poll = await session.get(GroupEmailPollState, sg.id)
    if poll:
        poll.last_uid = 0
        poll.bootstrapped = False
    else:
        session.add(GroupEmailPollState(study_group_id=sg.id, last_uid=0, bootstrapped=False))
    await state.clear()
    await message.answer("Сохранено!")
    if db_user:
        await _send_mail_menu(message, session, db_user, True)
