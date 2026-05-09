"""Перечитать письма с IMAP и обновить body_preview (после смены логики извлечения текста)."""

from __future__ import annotations

import email
import imaplib
import logging
from email import policy

import config
from database import GroupEmailMessage, StudyGroup
from utils.group_email_imap import (
    _extract_attachments,
    _extract_text,
    _message_id_from,
    _raw_bytes_from_fetch,
)

logger = logging.getLogger(__name__)


def refresh_previews_for_group(
    sg: StudyGroup, rows: list[GroupEmailMessage]
) -> dict[int, tuple[str, list[dict]]]:
    """
    Обойти INBOX, сопоставить по Message-ID.
    Возвращает {id записи в БД: (новый body_preview, вложения как в IMAP)}.
    Письма, удалённые с сервера, пропускаются.
    """
    if not rows:
        return {}
    want_by_mid: dict[str, int] = {}
    for r in rows:
        mid = (r.message_id_header or "").strip()
        if mid:
            want_by_mid[mid] = r.id

    if not want_by_mid:
        return {}

    login = (sg.corporate_email or "").strip()
    password = (sg.imap_password or "").strip()
    if not login or not password:
        return {}

    host = config.resolve_imap_host(login)
    if config.IMAP_USE_SSL:
        M = imaplib.IMAP4_SSL(host, config.IMAP_PORT)
    else:
        M = imaplib.IMAP4(host, config.IMAP_PORT)
    out: dict[int, tuple[str, list[dict]]] = {}
    try:
        M.login(login, password)
        M.select("INBOX", readonly=True)
        typ, data = M.uid("SEARCH", None, "ALL")
        if typ != "OK" or not data or not data[0]:
            logger.warning("IMAP refresh группа %s: пустой SEARCH", sg.id)
            return out
        raw_uids = data[0].split()
        for uid_b in raw_uids:
            uid_str = uid_b.decode() if isinstance(uid_b, bytes) else str(uid_b)
            typ_f, fetch_data = M.uid("FETCH", uid_str, "(RFC822)")
            raw_bytes: bytes | None = None
            if typ_f == "OK" and fetch_data:
                raw_bytes = _raw_bytes_from_fetch(fetch_data)
            if not raw_bytes:
                typ2, data2 = M.uid("FETCH", uid_str, "(BODY.PEEK[])")
                if typ2 == "OK" and data2:
                    raw_bytes = _raw_bytes_from_fetch(data2)
            if not raw_bytes:
                continue
            try:
                msg = email.message_from_bytes(raw_bytes, policy=policy.default)
            except Exception:
                continue
            mid = _message_id_from(msg, uid_str).strip()
            ge_id = want_by_mid.get(mid)
            if ge_id is None:
                continue
            body = _extract_text(msg)[:3500]
            atts = _extract_attachments(msg)
            out[ge_id] = (body, atts)
    finally:
        try:
            M.logout()
        except Exception:
            pass
    return out
