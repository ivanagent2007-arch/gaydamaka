import html
from datetime import date, datetime
from zoneinfo import ZoneInfo

import config

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.state import default_state
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from database import Grade, SiteGrade, User, UserRole
from handlers.states import GradeStates
from utils.group_context import get_study_group
from utils.attendance_summary import (
    aggregate_attendance,
    attendance_slots_by_subject_key,
    semester_subject_keys,
)
from utils.group_semesters import (
    semester_numbers_for_group,
    subject_key,
    subjects_for_group_semester,
)

router = Router(name="grades")


async def _show_semester_list(
    target, session, study_group_id: int, *, is_edit: bool = False
) -> None:
    """Показать inline-кнопки с семестрами. target — Message или CallbackQuery.message."""
    semesters = await semester_numbers_for_group(session, study_group_id)
    if not semesters:
        text = "Семестры появятся после загрузки расписания. Староста: /update_schedule"
        if is_edit:
            await target.edit_text(text)
        else:
            await target.answer(text)
        return
    rows = [
        [InlineKeyboardButton(text=f"Семестр {n}", callback_data=f"mg:{n}")]
        for n in semesters[:40]
    ]
    text = "Выбери семестр, чтобы посмотреть баллы:"
    if is_edit:
        await target.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    else:
        await target.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.message(Command("my_grades"))
@router.message(Command("grades"))
async def cmd_my_grades(message: Message, session, db_user: User | None) -> None:
    """Просмотр своих баллов по семестрам (студент и староста)."""
    if not db_user or not db_user.study_group_id:
        await message.answer("Сначала вступи в группу: /groups")
        return

    has_site = await session.scalar(
        select(SiteGrade.id)
        .where(SiteGrade.study_group_id == db_user.study_group_id)
        .limit(1)
    )
    if has_site:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="От старосты", callback_data="grades_source:elder"),
                InlineKeyboardButton(text="С сайта", callback_data="grades_source:site"),
            ],
        ])
        await message.answer("Выбери источник баллов:", reply_markup=kb)
    else:
        await _show_semester_list(message, session, db_user.study_group_id)


@router.callback_query(F.data == "grades_source:elder")
async def grades_source_elder(
    query: CallbackQuery, session, db_user: User | None
) -> None:
    if not db_user or not db_user.study_group_id:
        await query.answer("Нет группы", show_alert=True)
        return
    if query.message:
        await _show_semester_list(
            query.message, session, db_user.study_group_id, is_edit=True
        )
    await query.answer()


@router.callback_query(F.data.startswith("mg:"))
async def my_grades_show(query: CallbackQuery, session, db_user: User | None) -> None:
    if not db_user or not db_user.study_group_id:
        await query.answer("Нет группы", show_alert=True)
        return
    try:
        sem = int((query.data or "").split(":")[1])
    except (IndexError, ValueError):
        await query.answer()
        return
    q = await session.scalars(
        select(Grade)
        .where(
            Grade.user_id == db_user.id,
            Grade.semester_number == sem,
        )
        .order_by(Grade.subject)
    )
    rows = list(q)
    slots: dict[str, tuple[int, int]] = {}
    overall: dict[str, int | None] | None = None
    if db_user.study_group_id:
        slots = await attendance_slots_by_subject_key(
            session,
            db_user.study_group_id,
            db_user.id,
            datetime.now(ZoneInfo(config.TZ)).date(),
        )
        cat_keys = await semester_subject_keys(session, db_user.study_group_id, sem)
        overall = aggregate_attendance(slots, cat_keys)

    if not rows:
        text = f"Семестр {sem}: баллов пока нет."
    else:
        lines = [f"<b>Семестр {sem}</b>"]
        for g in rows:
            sub = html.escape(g.subject or "")
            pres, tot = slots.get(g.subject_key, (0, 0))
            if tot > 0:
                ap = round(100 * pres / tot)
                att_part = f", посещ. <b>{ap}</b> б. ({pres}/{tot})"
            else:
                att_part = ", посещ. —"
            lines.append(f"{sub}: <b>{g.points}</b>{att_part}")
        if overall and overall.get("total", 0) > 0 and overall.get("points") is not None:
            lines.append(
                f"Итого посещаемость: <b>{overall['points']}</b> б. "
                f"({overall['present']}/{overall['total']})"
            )
        text = "\n".join(lines)
    if query.message:
        await query.message.answer(text, parse_mode="HTML")
    await query.answer()


@router.message(F.text == "Добавить баллы", StateFilter(default_state))
async def grades_start(
    message: Message, state: FSMContext, is_elder: bool, session, db_user: User | None
) -> None:
    if not is_elder:
        await message.answer("Только староста.")
        return
    sg = await get_study_group(session, db_user)
    if not sg:
        await message.answer("Сначала создай или выбери учебную группу.")
        return
    q = await session.scalars(
        select(User).where(
            User.study_group_id == sg.id,
            User.role == UserRole.student,
        )
    )
    students = list(q)
    if not students:
        await message.answer("Нет студентов в группе (роль student).")
        return
    rows = [
        [
            InlineKeyboardButton(
                text=u.full_name[:32],
                callback_data=f"gr_u:{u.id}",
            )
        ]
        for u in students[:40]
    ]
    await message.answer("Выбери студента:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await state.set_state(GradeStates.pick_user)


@router.callback_query(GradeStates.pick_user, F.data.startswith("gr_u:"))
async def grades_after_pick_user(
    query: CallbackQuery, state: FSMContext, session, db_user: User | None
) -> None:
    uid = int(query.data.split(":")[1])
    await state.update_data(target_user_id=uid)
    sg = await get_study_group(session, db_user)
    if not sg:
        await query.answer("Нет учебной группы.", show_alert=True)
        await state.clear()
        return
    semesters = await semester_numbers_for_group(session, sg.id)
    if not semesters:
        await query.message.answer(
            "Каталог семестров пуст. Загрузи расписание: /update_schedule"
        )
        await state.clear()
        await query.answer()
        return
    await state.update_data(semester_options=semesters)
    rows = [
        [InlineKeyboardButton(text=f"Семестр {n}", callback_data=f"gr_sem:{n}")]
        for n in semesters[:40]
    ]
    await query.message.answer(
        "Выбери семестр, за который ставишь баллы:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await state.set_state(GradeStates.pick_semester)
    await query.answer()


@router.callback_query(GradeStates.pick_semester, F.data.startswith("gr_sem:"))
async def grades_after_pick_semester(
    query: CallbackQuery, state: FSMContext, session, db_user: User | None
) -> None:
    sem = int(query.data.split(":")[1])
    await state.update_data(semester_number=sem)
    sg = await get_study_group(session, db_user)
    if not sg:
        await query.answer("Нет группы", show_alert=True)
        await state.clear()
        return
    subjects = await subjects_for_group_semester(session, sg.id, sem)
    if not subjects:
        await query.message.answer(
            f"В семестре {sem} нет предметов в каталоге. "
            "Обнови расписание: /update_schedule"
        )
        await state.clear()
        await query.answer()
        return
    await state.update_data(subject_options=subjects)
    rows: list[list[InlineKeyboardButton]] = []
    for i, subj in enumerate(subjects[:80]):
        label = subj if len(subj) <= 48 else subj[:47] + "…"
        rows.append(
            [InlineKeyboardButton(text=label, callback_data=f"gr_s:{i}")]
        )
    await query.message.answer(
        f"Семестр {sem}. Выбери предмет:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await state.set_state(GradeStates.pick_subject)
    await query.answer()


@router.callback_query(GradeStates.pick_subject, F.data.startswith("gr_s:"))
async def grades_pick_subject_cb(query: CallbackQuery, state: FSMContext) -> None:
    idx = int(query.data.split(":")[1])
    data = await state.get_data()
    subjects: list[str] = data.get("subject_options") or []
    if idx < 0 or idx >= len(subjects):
        await query.answer("Неверный выбор", show_alert=True)
        return
    await state.update_data(subject=subjects[idx])
    await query.message.answer("Введи количество баллов (число, можно с точкой):")
    await state.set_state(GradeStates.points)
    await query.answer()


@router.message(GradeStates.points)
async def grades_points(message: Message, state: FSMContext, session, db_user: User | None) -> None:
    try:
        pts = float(message.text.replace(",", "."))
    except ValueError:
        await message.answer("Нужно число. Повтори.")
        return
    data = await state.get_data()
    target_id = data["target_user_id"]
    subject = data["subject"]
    semester_number = data.get("semester_number")
    if semester_number is None:
        await message.answer("Сессия сброшена. Начни снова: «Добавить баллы».")
        await state.clear()
        return
    sg = await get_study_group(session, db_user)
    if not sg:
        await message.answer("Группа не найдена.")
        await state.clear()
        return
    allowed = await subjects_for_group_semester(session, sg.id, int(semester_number))
    if subject not in allowed:
        await message.answer(
            "Этого предмета нет в выбранном семестре. Начни снова: «Добавить баллы»."
        )
        await state.clear()
        return
    user = await session.get(User, target_id)
    if not user:
        await message.answer("Пользователь не найден.")
        await state.clear()
        return
    sk = subject_key(subject)
    existing = await session.scalar(
        select(Grade).where(
            Grade.user_id == user.id,
            Grade.semester_number == int(semester_number),
            Grade.subject_key == sk,
        )
    )
    if existing:
        existing.points = pts
        existing.subject = subject
    else:
        session.add(
            Grade(
                user_id=user.id,
                semester_number=int(semester_number),
                subject=subject,
                subject_key=sk,
                points=pts,
            )
        )
    await message.answer(
        f"Сохранено: {user.full_name} — семестр {semester_number}, {subject}: {pts}"
    )
    await state.clear()
