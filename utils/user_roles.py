"""Единая проверка прав старосты: список в .env и роль в БД (после онбординга)."""

from __future__ import annotations

import config
from database import User, UserRole


def effective_is_elder(db_user: User | None, telegram_id: int, username: str | None) -> bool:
    """Староста или заместитель (и глобальные ID из .env)."""
    if config.is_elder(telegram_id, username):
        return True
    if db_user is not None and db_user.role in (
        UserRole.elder,
        UserRole.deputy_elder,
    ):
        return True
    return False


def effective_can_kick_members(
    db_user: User | None, telegram_id: int, username: str | None
) -> bool:
    """Исключать студентов может только настоящий староста (не зам)."""
    if config.is_elder(telegram_id, username):
        return True
    if db_user is not None and db_user.role == UserRole.elder:
        return True
    return False


def user_can_kick_members(user: User | None) -> bool:
    """По объекту User из БД — для исключения из группы."""
    if user is None:
        return False
    if config.is_elder(user.telegram_id, None):
        return True
    return user.role == UserRole.elder
