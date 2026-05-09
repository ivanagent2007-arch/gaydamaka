"""Сохранение вложений писем на диск + записи в БД."""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

import config
from database import GroupEmailAttachment
from utils.group_email_imap import _sanitize_filename

logger = logging.getLogger(__name__)

_ATTACH_ROOT = "data/group_email_attach"


def _attachment_dir(study_group_id: int, message_id: int) -> Path:
    return config.BASE_DIR / _ATTACH_ROOT / str(study_group_id) / str(message_id)


async def replace_message_attachments(
    session: AsyncSession,
    study_group_id: int,
    message_id: int,
    attachments: list[dict],
) -> None:
    """
    Удаляет старые вложения письма (строки БД и каталог на диске), сохраняет новые.
    attachments — элементы как из IMAP: filename, data (bytes), mime.
    """
    await session.execute(
        delete(GroupEmailAttachment).where(GroupEmailAttachment.message_id == message_id)
    )
    await session.flush()

    root = _attachment_dir(study_group_id, message_id)
    if root.is_dir():
        shutil.rmtree(root, ignore_errors=True)

    if not attachments:
        return

    root.mkdir(parents=True, exist_ok=True)
    base_resolved = config.BASE_DIR.resolve()

    for i, att in enumerate(attachments):
        data = att.get("data")
        if not data or not isinstance(data, (bytes, bytearray)):
            continue
        raw_fn = att.get("filename") or "file.bin"
        safe = _sanitize_filename(str(raw_fn))
        stored_name = f"{i:02d}_{safe}"
        dest = root / stored_name
        try:
            dest.write_bytes(bytes(data))
        except OSError as ex:
            logger.warning("Не удалось записать вложение %s: %s", stored_name, ex)
            continue
        try:
            rel = dest.resolve().relative_to(base_resolved).as_posix()
        except ValueError:
            logger.error("Вложение вне BASE_DIR: %s", dest)
            continue
        mime = (att.get("mime") or "application/octet-stream").strip()[:128]
        session.add(
            GroupEmailAttachment(
                message_id=message_id,
                filename=str(raw_fn)[:255],
                stored_path=rel[:512],
                mime_type=mime,
                size_bytes=len(data),
            )
        )


def is_safe_attachment_stored_path(stored_path: str) -> bool:
    """Путь из БД ведёт только внутрь data/group_email_attach/."""
    if not stored_path or ".." in stored_path or stored_path.startswith("/"):
        return False
    p = (config.BASE_DIR / stored_path).resolve()
    try:
        anchor = (config.BASE_DIR / _ATTACH_ROOT).resolve()
        p.relative_to(anchor)
    except ValueError:
        return False
    return True


def ascii_download_filename(name: str) -> str:
    """Имя для заголовка Content-Disposition (без кавычек и не-ASCII)."""
    s = (name or "file").replace("\x00", "").strip()
    s = re.sub(r'[\r\n"\\]', "_", s)
    out = "".join(c if 32 <= ord(c) < 127 and c not in '<>:' else "_" for c in s)
    return (out[:180] or "file").strip("._") or "file"
