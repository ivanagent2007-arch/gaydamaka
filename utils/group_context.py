"""Общая логика: текущая учебная группа пользователя."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from database import StudyGroup, User


async def get_study_group(session: AsyncSession, user: User | None) -> StudyGroup | None:
    if not user or not user.study_group_id:
        return None
    return await session.scalar(
        select(StudyGroup).where(StudyGroup.id == user.study_group_id)
    )


def ruz_search_for_group(sg: StudyGroup) -> str:
    return (sg.ruz_group_search or sg.name or "").strip()


def ruz_base_url_for_group(sg: StudyGroup) -> str | None:
    """Base URL РУЗ для группы (None → глобальный из config)."""
    return (sg.ruz_base_url or "").strip() or None
