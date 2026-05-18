"""FSM-хранилище aiogram на базе SQLAlchemy — состояние диалогов переживает рестарт бота.

Используется в main.py вместо MemoryStorage. Хранит по одной строке на сочетание
``(bot_id, chat_id, user_id, thread_id, business_connection_id)``; пустые
состояния и пустые данные удаляются, чтобы таблица не разрасталась.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Mapping

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StorageKey, StateType
from sqlalchemy import delete, select

from database import FSMStateRow, async_session_maker
from utils.db_context import commit_outer_session_if_any

logger = logging.getLogger(__name__)


def _state_to_str(state: StateType) -> str | None:
    if state is None:
        return None
    if isinstance(state, State):
        return state.state
    return str(state)


def _pk_tuple(key: StorageKey) -> tuple[int, int, int, int, str]:
    return (
        int(key.bot_id),
        int(key.chat_id),
        int(key.user_id),
        int(key.thread_id or 0),
        str(key.business_connection_id or ""),
    )


class SqlAlchemyStorage(BaseStorage):
    """Минималистичное FSM-хранилище: одна сессия БД на операцию,
    запись/удаление атомарны на уровне коммита."""

    async def _load(self, session, key: StorageKey) -> FSMStateRow | None:
        bid, cid, uid, tid, bcid = _pk_tuple(key)
        return await session.scalar(
            select(FSMStateRow).where(
                FSMStateRow.bot_id == bid,
                FSMStateRow.chat_id == cid,
                FSMStateRow.user_id == uid,
                FSMStateRow.thread_id == tid,
                FSMStateRow.business_connection_id == bcid,
            )
        )

    async def _delete(self, session, key: StorageKey) -> None:
        bid, cid, uid, tid, bcid = _pk_tuple(key)
        await session.execute(
            delete(FSMStateRow).where(
                FSMStateRow.bot_id == bid,
                FSMStateRow.chat_id == cid,
                FSMStateRow.user_id == uid,
                FSMStateRow.thread_id == tid,
                FSMStateRow.business_connection_id == bcid,
            )
        )

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        # На SQLite outer-сессия middleware могла держать write-lock — освободим
        # его до открытия своей сессии, иначе self-deadlock.
        await commit_outer_session_if_any()
        state_str = _state_to_str(state)
        async with async_session_maker() as session:
            row = await self._load(session, key)
            if row is None:
                if state_str is None:
                    return
                bid, cid, uid, tid, bcid = _pk_tuple(key)
                session.add(
                    FSMStateRow(
                        bot_id=bid,
                        chat_id=cid,
                        user_id=uid,
                        thread_id=tid,
                        business_connection_id=bcid,
                        state=state_str,
                        data_json="{}",
                        updated_at=datetime.utcnow(),
                    )
                )
            else:
                # Если и state, и data пусты — выкидываем строку, чтобы не копить мусор.
                if state_str is None and (row.data_json or "{}") in ("{}", ""):
                    await self._delete(session, key)
                else:
                    row.state = state_str
                    row.updated_at = datetime.utcnow()
            await session.commit()

    async def get_state(self, key: StorageKey) -> str | None:
        # get_state — это SELECT, на SQLite в WAL-режиме читатели не блокируются
        # writer'ом, так что коммит outer-сессии тут не нужен.
        async with async_session_maker() as session:
            row = await self._load(session, key)
            return row.state if row else None

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        await commit_outer_session_if_any()
        payload = json.dumps(dict(data), ensure_ascii=False)
        async with async_session_maker() as session:
            row = await self._load(session, key)
            if row is None:
                if payload == "{}":
                    return
                bid, cid, uid, tid, bcid = _pk_tuple(key)
                session.add(
                    FSMStateRow(
                        bot_id=bid,
                        chat_id=cid,
                        user_id=uid,
                        thread_id=tid,
                        business_connection_id=bcid,
                        state=None,
                        data_json=payload,
                        updated_at=datetime.utcnow(),
                    )
                )
            else:
                if payload == "{}" and row.state is None:
                    await self._delete(session, key)
                else:
                    row.data_json = payload
                    row.updated_at = datetime.utcnow()
            await session.commit()

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        async with async_session_maker() as session:
            row = await self._load(session, key)
            if not row or not row.data_json:
                return {}
            try:
                parsed = json.loads(row.data_json)
            except json.JSONDecodeError:
                logger.warning("FSM data_json повреждён для key=%s — игнорируем", key)
                return {}
            return parsed if isinstance(parsed, dict) else {}

    async def close(self) -> None:
        return None
