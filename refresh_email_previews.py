"""Одноразово обновить body_preview и вложения у сохранённых писем (IMAP).

Запуск из каталога student_assistant_bot:
  .venv\\Scripts\\python refresh_email_previews.py

Нужны сеть, .env с DATABASE_URL и настроенная почта групп (как у планировщика).
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# каталог пакета — корень для импорта config, database
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlalchemy import select

import config  # noqa: E402
from database import GroupEmailMessage, StudyGroup, async_session_maker, init_db  # noqa: E402
from utils.group_email_attachments_store import replace_message_attachments  # noqa: E402
from utils.group_email_preview_refresh import refresh_previews_for_group  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)


async def main() -> None:
    await init_db()
    updated = 0
    scanned_groups = 0
    async with async_session_maker() as session:
        groups = list(
            (
                await session.scalars(
                    select(StudyGroup).where(
                        StudyGroup.corporate_email.isnot(None),
                        StudyGroup.imap_password.isnot(None),
                    )
                )
            ).all()
        )
        for sg in groups:
            if not str(sg.corporate_email or "").strip() or not str(
                sg.imap_password or ""
            ).strip():
                continue
            rows = list(
                (
                    await session.scalars(
                        select(GroupEmailMessage).where(
                            GroupEmailMessage.study_group_id == sg.id
                        )
                    )
                ).all()
            )
            if not rows:
                continue
            scanned_groups += 1
            log.info(
                "Группа «%s» (id=%s): писем в БД — %s, IMAP…",
                sg.name,
                sg.id,
                len(rows),
            )
            try:
                mapping = await asyncio.to_thread(refresh_previews_for_group, sg, rows)
            except Exception as ex:
                log.exception("Группа %s: ошибка IMAP: %s", sg.id, ex)
                await session.rollback()
                continue
            for ge_id, (body, atts) in mapping.items():
                ge = await session.get(GroupEmailMessage, ge_id)
                if ge and ge.study_group_id == sg.id:
                    ge.body_preview = body
                    await replace_message_attachments(session, sg.id, ge_id, atts)
                    updated += 1
            await session.commit()
            log.info("  обновлено записей: %s", len(mapping))

    log.info("Готово. Групп с почтой обработано: %s, всего обновлено писем: %s", scanned_groups, updated)


if __name__ == "__main__":
    asyncio.run(main())
