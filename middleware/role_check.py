from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.dispatcher.middlewares.user_context import UserContextMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, Update, User as TgUser

from database import User
from utils.user_roles import effective_can_kick_members, effective_is_elder


def _resolve_tg_user(event: TelegramObject) -> TgUser | None:
    if isinstance(event, Update):
        ctx = UserContextMiddleware.resolve_event_context(event)
        return ctx.user if ctx else None
    if isinstance(event, Message):
        return event.from_user
    if isinstance(event, CallbackQuery):
        return event.from_user
    return None


class RoleMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        tg_user = _resolve_tg_user(event)

        session = data.get("session")
        db_user = None
        if session and tg_user:
            from sqlalchemy import select

            db_user = await session.scalar(
                select(User).where(User.telegram_id == tg_user.id)
            )

        is_elder = False
        can_kick_members = False
        if tg_user:
            is_elder = effective_is_elder(db_user, tg_user.id, tg_user.username)
            can_kick_members = effective_can_kick_members(
                db_user, tg_user.id, tg_user.username
            )

        data["db_user"] = db_user
        data["is_elder"] = is_elder
        data["can_kick_members"] = can_kick_members
        return await handler(event, data)
