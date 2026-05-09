"""Удаление записи ДЗ и связанного дедлайна (староста)."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

import config
from database import Deadline, Homework, Schedule


def _path_is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def unlink_homework_stored_files(rel_paths: list[str]) -> None:
    """Удалить файлы с диска (внутри проекта, каталоги загрузок)."""
    br = config.BASE_DIR.resolve()
    roots = {config.UPLOAD_DIR.resolve(), (br / "data" / "uploads").resolve()}
    for rel in rel_paths:
        if not rel or ".." in rel.replace("\\", "/"):
            continue
        full = (config.BASE_DIR / rel).resolve()
        if not _path_is_under(full, br):
            continue
        if not any(_path_is_under(full, r) for r in roots if r.exists()):
            continue
        try:
            full.unlink(missing_ok=True)
        except OSError:
            pass


async def delete_homework_for_study_group(
    session: AsyncSession,
    homework_id: int,
    study_group_id: int,
) -> tuple[bool, str]:
    """
    Удалить Homework и дедлайн с homework_id; проверка, что пара из этой учебной группы.
    """
    hw = await session.get(Homework, homework_id)
    if not hw:
        return False, "Запись ДЗ не найдена."
    sid = hw.schedule_id
    if not sid:
        return False, "ДЗ не привязано к паре."
    sch = await session.get(Schedule, sid)
    if not sch or sch.study_group_id != study_group_id:
        return False, "Нет доступа к этому ДЗ."
    unlink_homework_stored_files(hw.file_list())
    await session.execute(delete(Deadline).where(Deadline.homework_id == homework_id))
    await session.delete(hw)
    return True, ""
