"""Состав учебной группы: исключение, выход, передача роли старосты."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import config
from database import StudyGroup, User, UserRole
from utils.user_roles import user_can_kick_members


async def get_group_member_rows(
    session: AsyncSession, study_group_id: int
) -> tuple[StudyGroup | None, list[User]]:
    sg = await session.get(StudyGroup, study_group_id)
    if not sg:
        return None, []
    q = await session.scalars(
        select(User)
        .where(User.study_group_id == study_group_id)
        .order_by(User.full_name)
    )
    return sg, list(q)


async def elder_remove_student(
    session: AsyncSession, actor: User, target_user_id: int
) -> tuple[bool, str]:
    """
    Староста снимает пользователя с учебной группы.
    Нельзя: себя, пользователя не из группы, другого старосту.
    Заместитель не может исключать.
    """
    if not user_can_kick_members(actor):
        return False, "Исключать может только староста."
    if not actor.study_group_id:
        return False, "Ты не в учебной группе."
    if actor.id == target_user_id:
        return False, "Нельзя исключить себя. Для выхода обратись к администратору бота."
    target = await session.get(User, target_user_id)
    if not target or target.study_group_id != actor.study_group_id:
        return False, "Пользователь не в твоей группе."
    if target.role == UserRole.elder:
        return False, "Нельзя исключить старосту через бота."
    target.study_group_id = None
    target.group_name = ""
    target.role = UserRole.student
    return True, "Исключён из группы."


async def set_group_deputy(
    session: AsyncSession, chief: User, target_user_id: int | None
) -> tuple[bool, str]:
    """
    Назначить заместителя старосты (только староста) или снять всех (user_id=None).
    В группе не больше одного зама.
    """
    if not user_can_kick_members(chief):
        return False, "Назначать заместителя может только староста."
    sg_id = chief.study_group_id
    if not sg_id:
        return False, "Ты не в учебной группе."

    cur_deputies = list(
        await session.scalars(
            select(User).where(
                User.study_group_id == sg_id,
                User.role == UserRole.deputy_elder,
            )
        )
    )

    if target_user_id is None:
        for u in cur_deputies:
            u.role = UserRole.student
        return True, "Заместитель снят."

    target = await session.get(User, target_user_id)
    if not target or target.study_group_id != sg_id:
        return False, "Пользователь не в твоей группе."
    if target.id == chief.id:
        return False, "Нельзя назначить себя заместителем."
    if target.role == UserRole.elder:
        return False, "Нельзя назначить старосту заместителем."

    if target.role == UserRole.deputy_elder:
        for u in cur_deputies:
            if u.id != target.id:
                u.role = UserRole.student
        return True, "Заместитель уже назначен."

    if target.role != UserRole.student:
        return False, "Можно назначить только студента."

    for u in cur_deputies:
        u.role = UserRole.student
    target.role = UserRole.deputy_elder
    return True, "Заместитель назначен."


async def transfer_elder_role(
    session: AsyncSession, current_elder: User, target_user_id: int
) -> tuple[bool, str]:
    """Текущий староста передаёт свою роль другому участнику группы.
    Старый староста становится обычным студентом, новый — старостой.
    Если у группы был зам — он остаётся замом (на случай если зам и есть target,
    его роль становится elder, прежнего зама больше нет, и это нормально).
    """
    if current_elder.role != UserRole.elder:
        return False, "Передать роль может только действующий староста."
    sg_id = current_elder.study_group_id
    if not sg_id:
        return False, "Ты не в учебной группе."
    if current_elder.id == target_user_id:
        return False, "Нельзя передать роль самому себе."

    target = await session.get(User, target_user_id)
    if not target or target.study_group_id != sg_id:
        return False, "Пользователь не в твоей группе."

    target.role = UserRole.elder
    current_elder.role = UserRole.student
    return True, f"Староста теперь: {target.full_name}."


async def user_leave_group(
    session: AsyncSession, user: User
) -> tuple[bool, str]:
    """Пользователь выходит из своей учебной группы. Староста с членами в группе
    обязан сперва передать роль (см. transfer_elder_role) — иначе отказ."""
    if not user.study_group_id:
        return False, "Ты не в учебной группе."

    if user.role == UserRole.elder:
        # Если в группе остался хоть один другой участник — без передачи нельзя.
        other = await session.scalar(
            select(User.id).where(
                User.study_group_id == user.study_group_id,
                User.id != user.id,
            )
        )
        if other is not None:
            return False, (
                "Сначала передай роль старосты другому участнику группы — "
                "после этого сможешь выйти."
            )

    user.study_group_id = None
    user.group_name = ""
    # Если бы выходящий был старостой/замом — гасим роль, чтобы он не остался
    # «старостой без группы» по флагу UserRole.
    if user.role in (UserRole.elder, UserRole.deputy_elder):
        user.role = UserRole.student
    return True, "Ты вышел из группы."
