"""Забор писем с IMAP для одной учебной группы (планировщик и ручное обновление)."""

from __future__ import annotations

import asyncio
import html
import logging

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import config
from database import (
    GroupEmailMailbox,
    GroupEmailMessage,
    GroupEmailPollState,
    StudyGroup,
    User,
)
from utils.group_email_attachments_store import replace_message_attachments
from utils.group_email_imap import imap_fetch_new

logger = logging.getLogger(__name__)


def _mailbox_id_for_sender(sender_norm: str, boxes: list[GroupEmailMailbox]) -> int | None:
    s = sender_norm.lower()
    for b in sorted(boxes, key=lambda x: x.id):
        if b.sender_email.strip().lower() == s:
            return b.id
    return None


async def poll_one_group_mail(bot: Bot, session: AsyncSession, sg: StudyGroup) -> tuple[int, str | None]:
    """
    Один цикл IMAP для группы. Не делает commit — вызывающий обязан commit/rollback.
    Возвращает (число новых писем, доставленных в Telegram, текст ошибки или None).
    """
    if (
        not sg.corporate_email
        or not sg.imap_password
        or not str(sg.corporate_email).strip()
        or not str(sg.imap_password).strip()
    ):
        return 0, None

    poll = await session.get(GroupEmailPollState, sg.id)
    if not poll:
        poll = GroupEmailPollState(study_group_id=sg.id, last_uid=0, bootstrapped=False)
        session.add(poll)
        await session.flush()

    boxes = list(
        (
            await session.scalars(
                select(GroupEmailMailbox).where(GroupEmailMailbox.study_group_id == sg.id)
            )
        ).all()
    )
    last_uid = poll.last_uid
    imap_host = config.resolve_imap_host((sg.corporate_email or "").strip())
    try:
        msgs, new_last, new_bootstrapped = await asyncio.to_thread(
            imap_fetch_new,
            imap_host,
            config.IMAP_PORT,
            config.IMAP_USE_SSL,
            sg.corporate_email.strip(),
            sg.imap_password,
            last_uid,
            poll.bootstrapped,
        )
    except Exception as ex:
        logger.warning("IMAP группа %s (%s): %s", sg.id, imap_host, ex)
        return 0, str(ex)

    poll.last_uid = new_last
    poll.bootstrapped = new_bootstrapped
    if msgs:
        logger.info(
            "IMAP группа %s: %s новых писем (last_uid→%s)",
            sg.id,
            len(msgs),
            new_last,
        )

    members = list(
        (await session.scalars(select(User).where(User.study_group_id == sg.id))).all()
    )

    delivered = 0
    for m in msgs:
        mid = m["message_id"]
        exists = await session.scalar(
            select(GroupEmailMessage.id).where(
                GroupEmailMessage.study_group_id == sg.id,
                GroupEmailMessage.message_id_header == mid,
            )
        )
        if exists:
            continue

        mb_id = _mailbox_id_for_sender(m["sender_norm"], boxes)
        mb_title: str | None = None
        if mb_id:
            ob = next((x for x in boxes if x.id == mb_id), None)
            mb_title = ob.title if ob else None

        ge = GroupEmailMessage(
            study_group_id=sg.id,
            mailbox_id=mb_id,
            message_id_header=mid,
            sender=m["sender_raw"][:512],
            subject=m["subject"],
            body_preview=m["body_preview"],
            received_at=m["received_at"],
        )
        session.add(ge)
        await session.flush()
        await replace_message_attachments(
            session, sg.id, ge.id, m.get("attachments") or []
        )

        base = (config.WEBAPP_PUBLIC_URL or "").strip().rstrip("/")
        reply_markup: InlineKeyboardMarkup | None = None
        if base.lower().startswith("https://"):
            reply_markup = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="📧 Открыть письмо",
                            web_app=WebAppInfo(url=f"{base}/mail.html?id={ge.id}"),
                        )
                    ]
                ]
            )

        box_line = f"📂 <b>{html.escape(mb_title)}</b>\n" if mb_title else ""
        no_https_hint = ""
        if not reply_markup:
            no_https_hint = (
                "\n\nПрочитать письмо: мини-приложение бота → «Ещё» → «Почта группы»."
            )
        text = (
            f"<b>Новое письмо</b> [{html.escape(sg.name)}]\n"
            f"{box_line}"
            f"От: {html.escape(m['sender_raw'][:500])}"
            f"{no_https_hint}"
        )
        for u in members:
            try:
                await bot.send_message(
                    u.telegram_id,
                    text,
                    parse_mode="HTML",
                    reply_markup=reply_markup,
                )
            except Exception as ex:
                logger.debug("mail notify %s: %s", u.telegram_id, ex)
        delivered += 1

    return delivered, None
