"""ContextVar для проброса текущей сессии БД из middleware в FSM-storage.

Зачем: на SQLite write-lock держится до конца транзакции (т.е. до коммита
DbSessionMiddleware). Если внутри хендлера происходит ``state.set_state(...)``,
SqlAlchemyStorage открывает **отдельную** сессию для записи в ``aiogram_fsm``,
и она ждёт тот же write-lock. Хендлер не может вернуться (он `await`-ит на FSM),
middleware не может закоммитить (хендлер не вернулся) — self-deadlock.

Через этот ContextVar storage заранее коммитит outer-сессию (а значит и снимает
lock) перед своей записью. Никакой ручной возни с ``commit()`` в каждом
хендлере больше не нужно — паттерн закрыт на уровне инфраструктуры.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

current_db_session: ContextVar["AsyncSession | None"] = ContextVar(
    "current_db_session", default=None
)


async def commit_outer_session_if_any() -> None:
    """Если в этом asyncio-таске есть активная outer-сессия с открытой транзакцией —
    закоммитить её, чтобы освободить write-lock БД. После commit транзакция считается
    закрытой; следующая запись через ту же сессию автоматически начнёт новую.

    Безопасно вызывать многократно — последующие commit'ы на пустой сессии no-op.
    """
    sess = current_db_session.get(None)
    if sess is None:
        return
    try:
        in_tx = sess.in_transaction()
    except Exception:  # noqa: BLE001
        in_tx = False
    if not in_tx:
        return
    try:
        await sess.commit()
    except Exception as ex:  # noqa: BLE001
        # Если outer в плохом состоянии — откатить, чтобы FSM-сессия хотя бы прошла.
        logger.warning(
            "commit_outer_session_if_any: commit failed (%s), rolling back",
            ex.__class__.__name__,
        )
        try:
            await sess.rollback()
        except Exception:
            pass
