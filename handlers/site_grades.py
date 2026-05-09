"""Хендлеры для работы с баллами org.fa.ru."""

from __future__ import annotations

import html
import json
import logging
from datetime import datetime
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import or_, select

from database import SiteGrade, User
from handlers.states import OrgSetupStates
from utils.group_context import get_study_group
from utils.org_fa_client import OrgFaAuthError, OrgFaClient

logger = logging.getLogger(__name__)

router = Router(name="site_grades")

COOKIE_HELP = (
    "<b>Как получить cookies:</b>\n"
    "1. Открой <a href=\"https://org.fa.ru/app/attendance-book/journals\">org.fa.ru</a> "
    "и войди (с 2FA через MAX)\n"
    "2. Нажми F12 → вкладка <b>Network</b> (Сеть)\n"
    "3. Обнови страницу (F5)\n"
    "4. Нажми на любой запрос к <code>org.fa.ru</code>\n"
    "5. В разделе <b>Headers</b> найди строку <b>Cookie:</b>\n"
    "6. Скопируй всё значение (длинную строку) и отправь сюда"
)


def _journal_attendance_percent(raw: dict[str, Any]) -> int | None:
    """Процент посещаемости из объекта студента/журнала org.fa.ru (если API отдал поле)."""
    if not raw:
        return None
    for key in (
        "attendance_percent",
        "attendancePercent",
        "perc_attendance",
        "pass_percent",
        "PASS_PERCENT",
        "percent",
        "PERCENT",
    ):
        v = raw.get(key)
        if v is None:
            continue
        try:
            x = float(v)
        except (TypeError, ValueError):
            continue
        if 0 < x <= 1.0001:
            x *= 100.0
        if 0.0 <= x <= 100.0:
            return max(0, min(100, int(round(x))))
    return None


def _format_marks(marks_json_str: str | None) -> str:
    """Форматирует JSON с оценками в читаемую строку: T1=14, T2=18."""
    if not marks_json_str:
        return ""
    try:
        marks = json.loads(marks_json_str)
    except (json.JSONDecodeError, TypeError):
        return ""
    if not marks:
        return ""
    parts = [f"{m['type']}={int(m['val'])}" for m in marks if m.get("val") is not None]
    return ", ".join(parts)


# ── Elder: setup cookies ──────────────────────────────────────────────


@router.message(Command("setup_org"))
async def cmd_setup_org(
    message: Message,
    state: FSMContext,
    is_elder: bool,
    session,
    db_user: User | None,
) -> None:
    if not is_elder:
        await message.answer("Только староста может настраивать org.fa.ru.")
        return
    sg = await get_study_group(session, db_user)
    if not sg:
        await message.answer("Сначала создай или выбери учебную группу.")
        return
    await message.answer(COOKIE_HELP, parse_mode="HTML", disable_web_page_preview=True)
    await state.set_state(OrgSetupStates.cookies)


@router.message(OrgSetupStates.cookies)
async def org_setup_cookies(
    message: Message,
    state: FSMContext,
    session,
    db_user: User | None,
) -> None:
    raw_cookies = (message.text or "").strip()
    if not raw_cookies or "=" not in raw_cookies:
        await message.answer("Это не похоже на cookies. Попробуй ещё раз или /menu для отмены.")
        return

    sg = await get_study_group(session, db_user)
    if not sg:
        await message.answer("Группа не найдена.")
        await state.clear()
        return

    try:
        await message.delete()
    except Exception:
        pass

    status_msg = await message.answer("Проверяю cookies…")

    client = OrgFaClient()
    try:
        await client.auth_with_cookies(raw_cookies)
        students = await client.get_my_group()
    except OrgFaAuthError as exc:
        await status_msg.edit_text(
            f"Ошибка: {exc}\nПопробуй снова: /setup_org"
        )
        await state.clear()
        return
    except Exception as exc:
        logger.exception("org.fa.ru cookie auth error")
        await status_msg.edit_text(
            f"Ошибка подключения: {exc}\nПопробуй снова: /setup_org"
        )
        await state.clear()
        return
    finally:
        await client.close()

    sg.org_cookies = raw_cookies

    await status_msg.edit_text(
        f"Cookies org.fa.ru сохранены и проверены.\n"
        f"Найдено студентов в группе: {len(students)}\n\n"
        f"Теперь загрузи баллы: /sync_site_grades\n\n"
        f"Если cookies перестанут работать — повтори /setup_org."
    )
    await state.clear()


# ── Elder: sync grades ────────────────────────────────────────────────


@router.message(Command("sync_site_grades"))
async def cmd_sync_site_grades(
    message: Message,
    is_elder: bool,
    session,
    db_user: User | None,
) -> None:
    if not is_elder:
        await message.answer("Только староста.")
        return
    sg = await get_study_group(session, db_user)
    if not sg or not sg.org_cookies:
        await message.answer("Сначала настрой доступ: /setup_org")
        return

    status = await message.answer("Подключаюсь к org.fa.ru…")

    client = OrgFaClient()
    try:
        await client.auth_with_cookies(sg.org_cookies)
        await status.edit_text("Получаю список студентов…")
        students = await client.get_my_group()

        if not students:
            await status.edit_text(
                "Не удалось получить список студентов.\n"
                "Возможно, cookies устарели — обнови: /setup_org"
            )
            return

        student_map = {s.id: s for s in students}

        await status.edit_text("Получаю список дисциплин…")
        first_grades = await client.get_student_grades(students[0].id)
        if not first_grades or not first_grades.disciplines:
            await status.edit_text("Не удалось получить список дисциплин.")
            return

        disciplines = [
            d for d in first_grades.disciplines if d.discipline_id is not None
        ]

        await status.edit_text(
            f"Загружаю баллы по {len(disciplines)} дисциплинам…"
        )

        records_saved = 0
        for disc in disciplines:
            marks_by_student = await client.get_journal_marks(disc.discipline_id)
            journal_students = await client.get_journal_students(disc.discipline_id)
            journal_by_id = {js.id: js for js in journal_students}
            for js in journal_students:
                if js.id not in student_map:
                    student_map[js.id] = js

            all_student_ids = set(student_map.keys())
            for sid in all_student_ids:
                s = student_map[sid]
                marks = marks_by_student.get(sid, [])
                marks_data = [{"type": m.mark_type, "val": m.mark_val} for m in marks]
                total = sum(m.mark_val for m in marks) if marks else None
                js_row = journal_by_id.get(sid)
                att_pct = _journal_attendance_percent(js_row.raw if js_row else {})

                bot_user = await session.scalar(
                    select(User).where(
                        User.study_group_id == sg.id,
                        User.full_name == s.fullname,
                    )
                )
                tg_id = bot_user.telegram_id if bot_user else None

                existing = await session.scalar(
                    select(SiteGrade).where(
                        SiteGrade.study_group_id == sg.id,
                        SiteGrade.org_student_id == sid,
                        SiteGrade.discipline == disc.discipline_name,
                    )
                )
                if existing:
                    existing.student_name = s.fullname
                    if tg_id:
                        existing.telegram_id = tg_id
                    existing.total_score = total
                    existing.marks_json = json.dumps(marks_data, ensure_ascii=False) if marks_data else None
                    existing.attendance_percent = att_pct
                    existing.updated_at = datetime.utcnow()
                else:
                    session.add(
                        SiteGrade(
                            study_group_id=sg.id,
                            org_student_id=sid,
                            student_name=s.fullname,
                            telegram_id=tg_id,
                            discipline=disc.discipline_name,
                            attendance_percent=att_pct,
                            total_score=total,
                            marks_json=json.dumps(marks_data, ensure_ascii=False) if marks_data else None,
                        )
                    )
                records_saved += 1

        await status.edit_text(
            f"Синхронизация завершена.\n"
            f"Студентов: {len(student_map)}, дисциплин: {len(disciplines)}, "
            f"записей: {records_saved}"
        )
    except OrgFaAuthError as exc:
        await status.edit_text(
            f"Cookies устарели или невалидны: {exc}\nОбнови: /setup_org"
        )
    except Exception as exc:
        logger.exception("sync_site_grades error")
        await status.edit_text(f"Ошибка при синхронизации: {exc}")
    finally:
        await client.close()


# ── Student: view site grades (callback from /my_grades) ─────────────


@router.callback_query(F.data == "grades_source:site")
async def show_site_grades(
    query: CallbackQuery, session, db_user: User | None
) -> None:
    if not db_user or not db_user.study_group_id:
        await query.answer("Нет группы", show_alert=True)
        return

    grades = list(
        await session.scalars(
            select(SiteGrade)
            .where(
                SiteGrade.study_group_id == db_user.study_group_id,
                or_(
                    SiteGrade.telegram_id == db_user.telegram_id,
                    SiteGrade.student_name == db_user.full_name,
                ),
            )
            .order_by(SiteGrade.discipline)
        )
    )

    if not grades:
        text = (
            "Баллы с сайта org.fa.ru пока не загружены.\n"
            "Староста может загрузить: /sync_site_grades"
        )
    else:
        lines = ["<b>Баллы с сайта org.fa.ru</b>\n"]
        for g in grades:
            name = html.escape(g.discipline)
            marks_str = _format_marks(g.marks_json)
            total_str = f"{int(g.total_score)}" if g.total_score is not None else "—"
            if marks_str:
                lines.append(f"  {name}: <b>{total_str}</b> ({marks_str})")
            else:
                lines.append(f"  {name}: <b>{total_str}</b>")
        text = "\n".join(lines)

    if query.message:
        await query.message.answer(text, parse_mode="HTML")
    await query.answer()
