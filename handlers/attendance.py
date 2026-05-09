from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import default_state
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

import config
from database import Attendance, Schedule, StudyGroup, User
from handlers.states import ElderAttendanceStates
from utils.attendance_report import attendance_group_day_stats, build_attendance_report_text
from utils.scheduler import notify_elders_if_attendance_complete
from utils.group_context import get_study_group

router = Router(name="attendance")

# Сегодня и ещё 6 дней назад — окно, в котором можно отметиться на пару
_MARK_WINDOW_DAYS = 7


def _day_button_label(d: date, today: date) -> str:
    if d == today:
        return f"Сегодня {d.strftime('%d.%m')}"
    if d == today - timedelta(days=1):
        return f"Вчера {d.strftime('%d.%m')}"
    wd = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")[d.weekday()]
    return f"{wd} {d.strftime('%d.%m')}"


def _mark_window_start(today: date) -> date:
    return today - timedelta(days=_MARK_WINDOW_DAYS - 1)


def can_mark_attendance(lesson_date: date | None, today: date) -> bool:
    """Можно отметиться на пару, если дата пары в окне [сегодня−6 … сегодня]."""
    if not lesson_date:
        return False
    start = _mark_window_start(today)
    return start <= lesson_date <= today


def _date_keyboard(today: date) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for i in range(_MARK_WINDOW_DAYS):
        d = today - timedelta(days=i)
        rows.append(
            [
                InlineKeyboardButton(
                    text=_day_button_label(d, today),
                    callback_data=f"att_d:{d.isoformat()}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _today_tz() -> date:
    return datetime.now(ZoneInfo(config.TZ)).date()


def _elder_date_keyboard(today: date) -> InlineKeyboardMarkup:
    """Те же 7 дней, что и для отметки студентом, плюс своя дата и отмена."""
    rows: list[list[InlineKeyboardButton]] = []
    for i in range(_MARK_WINDOW_DAYS):
        d = today - timedelta(days=i)
        rows.append(
            [
                InlineKeyboardButton(
                    text=_day_button_label(d, today),
                    callback_data=f"el_att_d:{d.isoformat()}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="Своя дата (ДД.ММ.ГГГГ)",
                callback_data="el_att:custom",
            )
        ]
    )
    rows.append([InlineKeyboardButton(text="Отмена", callback_data="el_att:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _parse_custom_report_date(text: str) -> date | None:
    raw = (text or "").strip()
    for fmt in ("%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _custom_report_date_allowed(d: date, today: date) -> bool:
    return date(2015, 1, 1) <= d <= today + timedelta(days=366)


async def _lessons_keyboard_for_date(
    session, study_group_id: int, d: date
) -> tuple[list[Schedule], InlineKeyboardMarkup | None]:
    q = await session.scalars(
        select(Schedule)
        .where(
            Schedule.study_group_id == study_group_id,
            Schedule.lesson_date == d,
        )
        .order_by(Schedule.start_time)
    )
    lessons = list(q)
    if not lessons:
        return lessons, None
    buttons: list[list[InlineKeyboardButton]] = []
    for les in lessons:
        k = (les.lesson_kind or "").strip()
        c = (les.contingent_label or "").strip()
        subj = les.subject[:18] if (k or c) else les.subject[:28]
        kind_s = f" ({k})" if k else ""
        label = f"{les.start_time} {subj}{kind_s}"
        if c:
            label = f"{label} · {c}"[:64]
        else:
            label = label[:64]
        buttons.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"att:{les.id}",
                )
            ]
        )
    buttons.append(
        [InlineKeyboardButton(text="← Другая дата", callback_data="att_back")]
    )
    return lessons, InlineKeyboardMarkup(inline_keyboard=buttons)


async def send_mark_keyboard(
    message: Message, session, db_user: User | None
) -> None:
    if not db_user or not db_user.study_group_id:
        await message.answer("Сначала вступи в группу по коду старосты (/groups).")
        return
    today = date.today()
    await message.answer(
        "Выбери день, за который отмечаешься (сегодня и 6 предыдущих дней). "
        "На выбранную пару можно отметиться в течение этой недели.",
        reply_markup=_date_keyboard(today),
    )


@router.message(Command("mark_attendance"))
async def cmd_mark_attendance(
    message: Message, session, db_user: User | None
) -> None:
    await send_mark_keyboard(message, session, db_user)


@router.callback_query(F.data == "att_back")
async def cb_att_back(query: CallbackQuery, db_user: User | None) -> None:
    if not db_user or not db_user.study_group_id:
        await query.answer("Нет группы", show_alert=True)
        return
    today = date.today()
    if query.message:
        await query.message.edit_text(
            "Выбери день, за который отмечаешься (сегодня и 6 предыдущих дней). "
            "На выбранную пару можно отметиться в течение этой недели.",
            reply_markup=_date_keyboard(today),
        )
    await query.answer()


@router.callback_query(F.data.startswith("att_d:"))
async def cb_att_pick_date(query: CallbackQuery, session, db_user: User | None) -> None:
    if not db_user or not db_user.study_group_id:
        await query.answer("Нет группы", show_alert=True)
        return
    raw = (query.data or "").split(":", 1)
    if len(raw) < 2:
        await query.answer("Ошибка")
        return
    try:
        d = date.fromisoformat(raw[1])
    except ValueError:
        await query.answer("Неверная дата")
        return
    today = date.today()
    if not can_mark_attendance(d, today):
        await query.answer("Дата вне доступного окна", show_alert=True)
        return
    lessons, kb = await _lessons_keyboard_for_date(session, db_user.study_group_id, d)
    if not lessons:
        if query.message:
            await query.message.edit_text(
                f"На {d.strftime('%d.%m.%Y')} нет пар в расписании.\n"
                "Выбери другую дату.",
                reply_markup=_date_keyboard(today),
            )
        await query.answer()
        return
    wd = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")[d.weekday()]
    if query.message:
        await query.message.edit_text(
            f"{wd}, {d.strftime('%d.%m.%Y')} — выбери пару:",
            reply_markup=kb,
        )
    await query.answer()


@router.callback_query(F.data.startswith("att:"))
async def cb_attendance(query: CallbackQuery, session, db_user: User | None) -> None:
    try:
        sid = int(query.data.split(":")[1])
    except (IndexError, ValueError):
        await query.answer("Ошибка")
        return
    les = await session.get(Schedule, sid)
    uid = query.from_user.id
    user = await session.scalar(select(User).where(User.telegram_id == uid))
    if not les or not user or user.study_group_id != les.study_group_id:
        await query.answer("Пара не найдена")
        return
    today = date.today()
    if not can_mark_attendance(les.lesson_date, today):
        await query.answer(
            "Эту пару уже нельзя отметить (прошло больше недели с даты пары).",
            show_alert=True,
        )
        return
    mark_day = les.lesson_date or today
    existing = await session.scalar(
        select(Attendance).where(
            Attendance.user_id == user.id,
            Attendance.schedule_id == sid,
        )
    )
    if existing:
        await query.answer("Уже отмечен")
        return
    session.add(
        Attendance(
            user_id=user.id,
            schedule_id=sid,
            mark_date=mark_day,
            is_present=True,
        )
    )
    await session.flush()
    sg = await session.get(StudyGroup, les.study_group_id)
    if sg:
        await notify_elders_if_attendance_complete(query.bot, session, sg, mark_day)
    await query.answer("Отмечено!")
    if query.message:
        k = (les.lesson_kind or "").strip()
        c = (les.contingent_label or "").strip()
        kind_part = f", {k}" if k else ""
        cont_part = f". {c}" if c else ""
        await query.message.answer(
            f"Записано: {les.subject}{kind_part}{cont_part} ({les.start_time})"
        )


# ─── Староста: отчёт посещаемости за день ─────────────────────────────────


@router.message(ElderAttendanceStates.waiting_custom_date, ~F.text)
async def elder_att_custom_need_text(message: Message) -> None:
    await message.answer(
        "Пришли дату обычным текстом: <b>ДД.ММ.ГГГГ</b> (например <code>12.04.2026</code>).",
        parse_mode="HTML",
    )


@router.message(ElderAttendanceStates.waiting_custom_date, F.text & ~F.text.startswith("/"))
async def elder_att_custom_date_entered(
    message: Message,
    session,
    db_user: User | None,
    is_elder: bool,
    state: FSMContext,
) -> None:
    if not is_elder or not db_user or not db_user.study_group_id:
        await state.clear()
        await message.answer("Нет доступа.")
        return
    parsed = _parse_custom_report_date(message.text or "")
    if not parsed:
        await message.answer(
            "Не понял дату. Пример: <code>12.04.2026</code> или <code>12.04.26</code>",
            parse_mode="HTML",
        )
        return
    today = _today_tz()
    if not _custom_report_date_allowed(parsed, today):
        await message.answer(
            "Дата вне допустимого диапазона (примерно с 2015 года и не дальше чем на год вперёд)."
        )
        return
    sg = await get_study_group(session, db_user)
    if not sg:
        await state.clear()
        await message.answer("Группа не найдена.")
        return
    st = await attendance_group_day_stats(session, sg, parsed)
    if (
        st["lesson_slots_count"] > 0
        and st["students_total"] > 0
        and st["students_with_any_mark_count"] == 0
    ):
        await state.clear()
        await message.answer(
            "За эту дату никто из студентов ещё не отмечался на парах — список посещений не отправляю."
        )
        return
    text = await build_attendance_report_text(session, sg, parsed)
    await state.clear()
    await message.answer(text)


@router.message(F.text == "Отчёт посещаемости", StateFilter(default_state))
async def elder_att_open_picker(
    message: Message,
    db_user: User | None,
    is_elder: bool,
) -> None:
    if not is_elder or not db_user or not db_user.study_group_id:
        await message.answer(
            "Кнопка «Отчёт посещаемости» доступна старосте с выбранной учебной группой."
        )
        return
    today = _today_tz()
    await message.answer(
        "Выбери день для отчёта (сегодня и 6 предыдущих дней по часовому поясу бота) "
        "или нажми «Своя дата» и введи дату сообщением.",
        reply_markup=_elder_date_keyboard(today),
    )


@router.callback_query(F.data == "el_att:cancel")
async def cb_elder_att_cancel(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if query.message:
        try:
            await query.message.edit_text("Ок, отменено.")
        except Exception:
            await query.message.answer("Ок, отменено.")
    await query.answer()


@router.callback_query(F.data == "el_att:custom")
async def cb_elder_att_custom(
    query: CallbackQuery,
    db_user: User | None,
    is_elder: bool,
    state: FSMContext,
) -> None:
    if not is_elder or not db_user or not db_user.study_group_id:
        await query.answer("Только староста", show_alert=True)
        return
    await state.set_state(ElderAttendanceStates.waiting_custom_date)
    if query.message:
        await query.message.edit_text(
            "Введи дату отчёта в формате <b>ДД.ММ.ГГГГ</b> "
            "(например <code>12.04.2026</code>) или <b>ДД.ММ.ГГ</b> "
            "(например <code>12.04.26</code>).\n"
            "Чтобы выйти — нажми <b>Меню</b> внизу.",
            parse_mode="HTML",
            reply_markup=None,
        )
    await query.answer()


@router.callback_query(F.data.startswith("el_att_d:"))
async def cb_elder_att_pick_date(
    query: CallbackQuery,
    session,
    db_user: User | None,
    is_elder: bool,
    state: FSMContext,
) -> None:
    await state.clear()
    if not is_elder or not db_user or not db_user.study_group_id:
        await query.answer("Только староста", show_alert=True)
        return
    raw = (query.data or "").split(":", 1)
    if len(raw) < 2:
        await query.answer("Ошибка")
        return
    try:
        d = date.fromisoformat(raw[1])
    except ValueError:
        await query.answer("Неверная дата")
        return
    today = _today_tz()
    if not can_mark_attendance(d, today):
        await query.answer("Дата вне быстрого выбора (неделя от сегодня).", show_alert=True)
        return
    sg = await get_study_group(session, db_user)
    if not sg:
        await query.answer("Нет группы", show_alert=True)
        return
    st = await attendance_group_day_stats(session, sg, d)
    if (
        st["lesson_slots_count"] > 0
        and st["students_total"] > 0
        and st["students_with_any_mark_count"] == 0
    ):
        if query.message:
            try:
                await query.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
            await query.message.answer(
                "За эту дату никто из студентов ещё не отмечался на парах — список посещений не отправляю."
            )
        await query.answer()
        return
    text = await build_attendance_report_text(session, sg, d)
    if query.message:
        try:
            await query.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await query.message.answer(text)
    await query.answer()
