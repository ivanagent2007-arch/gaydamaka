import io
import json
from collections import defaultdict
from datetime import date, datetime, timedelta

import aiofiles
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import default_state
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from sqlalchemy import select

import config
from database import Homework, Schedule, User
from handlers.states import HomeworkDeleteStates, HomeworkStates
from utils.group_context import get_study_group
from utils.homework_deadline import deadline_for_homework
from utils.homework_delete import delete_homework_for_study_group

router = Router(name="homework")


async def _append_hw_file(message: Message, state: FSMContext, rel_path: str) -> None:
    data = await state.get_data()
    paths = list(data.get("hw_file_paths") or [])
    paths.append(rel_path)
    await state.update_data(hw_file_paths=paths)
    await message.answer(
        f"Вложение добавлено (всего файлов: {len(paths)}). "
        "Можно прислать ещё документ, фото или написать /skip."
    )


def _parse_date(raw: str) -> date | None:
    raw = (raw or "").strip()
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%d.%m"):
        try:
            d = datetime.strptime(raw, fmt).date()
            if fmt == "%d.%m":
                d = d.replace(year=date.today().year)
                if d < date.today() - timedelta(days=30):
                    d = d.replace(year=d.year + 1)
            return d
        except ValueError:
            continue
    return None


def _hw_subject_btn(l: Schedule) -> str:
    k = (l.lesson_kind or "").strip()
    c = (l.contingent_label or "").strip()
    bits: list[str] = []
    if k:
        bits.append(k)
    if c:
        bits.append(c[:20])
    tail = f" ({' · '.join(bits)})" if bits else ""
    line = f"{l.start_time} {l.subject[:18]}{tail}"
    return line[:64]


# ──────────────────────────────────────────────────────
#  Шаг 1: Выбор даты
# ──────────────────────────────────────────────────────

@router.message(F.text == "Добавить ДЗ к паре", StateFilter(default_state))
async def hw_start(
    message: Message, state: FSMContext, is_elder: bool, session, db_user: User | None
) -> None:
    if not is_elder:
        await message.answer("Только староста.")
        return
    sg = await get_study_group(session, db_user)
    if not sg:
        await message.answer("Сначала создай или выбери учебную группу.")
        return

    today = date.today()
    tomorrow = today + timedelta(days=1)
    quick_kb = ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="Сегодня"),
                KeyboardButton(text="Завтра"),
            ],
            [KeyboardButton(text="Меню")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await state.set_state(HomeworkStates.pick_date)
    await message.answer(
        "На какую дату добавить ДЗ?\n"
        "Выбери кнопкой или введи дату (<b>ДД.ММ</b> или <b>ДД.ММ.ГГГГ</b>).",
        parse_mode="HTML",
        reply_markup=quick_kb,
    )


@router.message(HomeworkStates.pick_date, F.text == "Меню")
async def hw_date_menu(
    message: Message, state: FSMContext, is_elder: bool, db_user: User | None,
) -> None:
    from handlers.common import cmd_menu
    await cmd_menu(message, is_elder, db_user, state)


@router.message(HomeworkStates.pick_date, F.text)
async def hw_pick_date(
    message: Message, state: FSMContext, session, db_user: User | None,
) -> None:
    raw = (message.text or "").strip()
    if raw == "Сегодня":
        day = date.today()
    elif raw == "Завтра":
        day = date.today() + timedelta(days=1)
    else:
        day = _parse_date(raw)
        if not day:
            await message.answer(
                "Не понял дату. Пример: <code>15.04</code> или <code>15.04.2026</code>",
                parse_mode="HTML",
            )
            return

    sg = await get_study_group(session, db_user)
    if not sg:
        await state.clear()
        await message.answer("Группа не найдена. /groups")
        return

    q = await session.scalars(
        select(Schedule)
        .where(
            Schedule.study_group_id == sg.id,
            Schedule.lesson_date == day,
        )
        .order_by(Schedule.start_time)
    )
    lessons = list(q)
    if not lessons:
        wd = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")[day.weekday()]
        await message.answer(
            f"На {day.strftime('%d.%m.%Y')} ({wd}) пар нет.\n"
            "Попробуй другую дату или обнови расписание: /update_schedule",
        )
        return

    rows = [
        [
            InlineKeyboardButton(
                text=_hw_subject_btn(l),
                callback_data=f"hw:{l.id}",
            )
        ]
        for l in lessons
    ]
    wd = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")[day.weekday()]
    await message.answer(
        f"Пары на {day.strftime('%d.%m.%Y')} ({wd}) — выбери предмет:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await state.set_state(HomeworkStates.pick_schedule)


# ──────────────────────────────────────────────────────
#  Шаг 2: Выбор предмета (inline-кнопка)
# ──────────────────────────────────────────────────────

@router.callback_query(HomeworkStates.pick_schedule, F.data.startswith("hw:"))
async def hw_pick(query: CallbackQuery, state: FSMContext) -> None:
    sid = int(query.data.split(":")[1])
    await state.update_data(schedule_id=sid)
    await query.message.answer(
        "Введи текст домашнего задания:",
        reply_markup=ReplyKeyboardRemove(),
    )
    await state.set_state(HomeworkStates.description)
    await query.answer()


# ──────────────────────────────────────────────────────
#  Шаг 3: Текст ДЗ
# ──────────────────────────────────────────────────────

@router.message(HomeworkStates.description)
async def hw_desc(message: Message, state: FSMContext) -> None:
    await state.update_data(description=message.text.strip(), hw_file_paths=[])
    await message.answer(
        "Прикрепи один или несколько файлов: <b>документ</b> или <b>фото</b> "
        f"(до {config.HOMEWORK_UPLOAD_MAX_MB} МБ на файл). "
        "Когда закончишь — напиши /skip.",
        parse_mode="HTML",
    )
    await state.set_state(HomeworkStates.file)


# ──────────────────────────────────────────────────────
#  Шаг 4: Файл (необязательно)
# ──────────────────────────────────────────────────────

@router.message(HomeworkStates.file, F.text == "/skip")
async def hw_skip_file(message: Message, state: FSMContext, session) -> None:
    data = await state.get_data()
    paths = list(data.get("hw_file_paths") or [])
    await _save_hw(message, state, session, paths)


@router.message(HomeworkStates.file, F.document)
async def hw_doc(message: Message, state: FSMContext, _session) -> None:
    doc = message.document
    if not doc.file_name:
        await message.answer("Нужен файл с именем.")
        return
    max_b = config.HOMEWORK_UPLOAD_MAX_BYTES
    if doc.file_size is not None and doc.file_size > max_b:
        await message.answer(
            f"Файл слишком большой. Максимум {config.HOMEWORK_UPLOAD_MAX_MB} МБ на один файл."
        )
        return
    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = "".join(c for c in doc.file_name if c.isalnum() or c in "._-")[:120]
    path = config.UPLOAD_DIR / f"{message.chat.id}_{doc.file_unique_id}_{safe_name}"
    buf = io.BytesIO()
    await message.bot.download(doc, destination=buf)
    raw = buf.getvalue()
    if len(raw) > max_b:
        await message.answer(
            f"Файл слишком большой. Максимум {config.HOMEWORK_UPLOAD_MAX_MB} МБ на один файл."
        )
        return
    async with aiofiles.open(path, "wb") as f:
        await f.write(raw)
    rel = str(path.relative_to(config.BASE_DIR)).replace("\\", "/")
    await _append_hw_file(message, state, rel)


@router.message(HomeworkStates.file, F.photo)
async def hw_photo(message: Message, state: FSMContext, _session) -> None:
    """Фото из чата (не как файл) — иначе Telegram не шлёт document."""
    photo = message.photo[-1]
    max_b = config.HOMEWORK_UPLOAD_MAX_BYTES
    if photo.file_size is not None and photo.file_size > max_b:
        await message.answer(
            f"Фото слишком большое. Максимум {config.HOMEWORK_UPLOAD_MAX_MB} МБ на один файл."
        )
        return
    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path = config.UPLOAD_DIR / f"{message.chat.id}_{photo.file_unique_id}_photo.jpg"
    buf = io.BytesIO()
    await message.bot.download(photo, destination=buf)
    raw = buf.getvalue()
    if len(raw) > max_b:
        await message.answer(
            f"Фото слишком большое. Максимум {config.HOMEWORK_UPLOAD_MAX_MB} МБ на один файл."
        )
        return
    async with aiofiles.open(path, "wb") as f:
        await f.write(raw)
    rel = str(path.relative_to(config.BASE_DIR)).replace("\\", "/")
    await _append_hw_file(message, state, rel)


@router.message(HomeworkStates.file, F.text)
async def hw_file_unknown_text(message: Message) -> None:
    await message.answer("Пришли документ, фото или напиши /skip чтобы сохранить ДЗ.")


async def _save_hw(message: Message, state: FSMContext, session, paths: list[str]) -> None:
    data = await state.get_data()
    sid = data.get("schedule_id")
    desc = data.get("description", "")
    if not sid:
        await message.answer("Сессия сброшена, начни сначала.")
        await state.clear()
        return
    hw = Homework(
        schedule_id=sid,
        description=desc,
        file_paths=json.dumps(paths, ensure_ascii=False),
    )
    session.add(hw)
    # commit (не flush): state.clear() ниже использует отдельную FSM-сессию,
    # которая зависнет в ожидании SQLite write-lock, если он не освобождён здесь.
    await session.commit()
    sch = await session.get(Schedule, sid)
    creator = await session.scalar(
        select(User).where(User.telegram_id == message.from_user.id)
    )
    if sch:
        session.add(
            deadline_for_homework(hw, sch, creator_user_id=creator.id if creator else None)
        )
    await message.answer("ДЗ сохранено — добавлено в дедлайны (до конца дня пары).")
    await state.clear()


# ──────────────────────────────────────────────────────
#  Удаление ДЗ (староста)
# ──────────────────────────────────────────────────────


@router.message(F.text == "Удалить ДЗ", StateFilter(default_state))
async def hwd_start(
    message: Message, state: FSMContext, is_elder: bool, session, db_user: User | None
) -> None:
    if not is_elder:
        await message.answer("Только староста.")
        return
    sg = await get_study_group(session, db_user)
    if not sg:
        await message.answer("Сначала создай или выбери учебную группу.")
        return
    quick_kb = ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="Сегодня"),
                KeyboardButton(text="Завтра"),
            ],
            [KeyboardButton(text="Меню")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await state.set_state(HomeworkDeleteStates.pick_date)
    await message.answer(
        "На какую дату удалить прикреплённое ДЗ?\n"
        "Выбери кнопкой или введи дату (<b>ДД.ММ</b> или <b>ДД.ММ.ГГГГ</b>).",
        parse_mode="HTML",
        reply_markup=quick_kb,
    )


@router.message(HomeworkDeleteStates.pick_date, F.text == "Меню")
async def hwd_date_menu(
    message: Message, state: FSMContext, is_elder: bool, db_user: User | None
) -> None:
    from handlers.common import cmd_menu

    await cmd_menu(message, is_elder, db_user, state)


@router.message(HomeworkDeleteStates.pick_date, F.text)
async def hwd_pick_date(
    message: Message, state: FSMContext, session, db_user: User | None
) -> None:
    raw = (message.text or "").strip()
    if raw == "Сегодня":
        day = date.today()
    elif raw == "Завтра":
        day = date.today() + timedelta(days=1)
    else:
        day = _parse_date(raw)
        if not day:
            await message.answer(
                "Не понял дату. Пример: <code>15.04</code> или <code>15.04.2026</code>",
                parse_mode="HTML",
            )
            return

    sg = await get_study_group(session, db_user)
    if not sg:
        await state.clear()
        await message.answer("Группа не найдена. /groups")
        return

    stmt = (
        select(Homework, Schedule)
        .join(Schedule, Homework.schedule_id == Schedule.id)
        .where(
            Schedule.study_group_id == sg.id,
            Schedule.lesson_date == day,
        )
        .order_by(Schedule.start_time, Homework.id)
    )
    res = await session.execute(stmt)
    pairs = list(res.all())
    if not pairs:
        wd = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")[day.weekday()]
        await message.answer(
            f"На {day.strftime('%d.%m.%Y')} ({wd}) нет прикреплённого ДЗ.\n"
            "Попробуй другую дату.",
        )
        return

    by_sid: dict[int, list[tuple[Homework, Schedule]]] = defaultdict(list)
    for hw, sch in pairs:
        by_sid[sch.id].append((hw, sch))

    rows: list[list[InlineKeyboardButton]] = []
    for _sid, lst in sorted(by_sid.items(), key=lambda x: (x[1][0][1].start_time or "", x[0])):
        for idx, (hw, sch) in enumerate(lst):
            base = _hw_subject_btn(sch)
            if len(lst) > 1:
                base = f"{base} ({idx + 1})"
            rows.append(
                [
                    InlineKeyboardButton(
                        text=base[:64],
                        callback_data=f"dhx:{hw.id}",
                    )
                ]
            )

    wd = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")[day.weekday()]
    await message.answer(
        f"Прикреплённое ДЗ на {day.strftime('%d.%m.%Y')} ({wd}) — что удалить?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(HomeworkDeleteStates.pick_date, F.data.startswith("dhx:"))
async def hwd_pick_hw(
    query: CallbackQuery, state: FSMContext, session, db_user: User | None
) -> None:
    try:
        hw_id = int((query.data or "").split(":")[1])
    except (IndexError, ValueError):
        await query.answer("Некорректная кнопка", show_alert=True)
        return
    sg = await get_study_group(session, db_user)
    hw = await session.get(Homework, hw_id) if sg else None
    sch = await session.get(Schedule, hw.schedule_id) if hw and hw.schedule_id else None
    if not sg or not hw or not sch or sch.study_group_id != sg.id:
        await query.answer("Нет доступа к этому ДЗ.", show_alert=True)
        return

    await state.set_state(HomeworkDeleteStates.confirm)
    await state.update_data(pending_hw_delete_id=hw_id)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да, удалить", callback_data=f"dhxy:{hw_id}"),
                InlineKeyboardButton(text="Отмена", callback_data="dhxn"),
            ]
        ]
    )
    subj = (sch.subject or "пара").strip()
    await query.message.answer(
        f"Удалить прикреплённое ДЗ к «{subj}» ({sch.start_time})? "
        "Файлы и дедлайн к этой паре будут убраны.",
        reply_markup=kb,
    )
    await query.answer()


@router.callback_query(HomeworkDeleteStates.confirm, F.data.startswith("dhxy:"))
async def hwd_confirm_yes(
    query: CallbackQuery, state: FSMContext, session, db_user: User | None
) -> None:
    try:
        hw_id = int((query.data or "").split(":")[1])
    except (IndexError, ValueError):
        await query.answer("Ошибка", show_alert=True)
        return
    data = await state.get_data()
    pending = data.get("pending_hw_delete_id")
    if pending != hw_id:
        await query.answer("Устаревшая кнопка, начни заново.", show_alert=True)
        await state.clear()
        return

    sg = await get_study_group(session, db_user)
    if not sg:
        await state.clear()
        await query.answer("Нет группы", show_alert=True)
        return

    ok, err = await delete_homework_for_study_group(session, hw_id, sg.id)
    if not ok:
        await query.answer(err[:200] if err else "Ошибка", show_alert=True)
        return

    await state.clear()
    try:
        await query.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    await query.message.answer("ДЗ удалено (вместе с дедлайном к этой паре).")
    await query.answer()


@router.callback_query(HomeworkDeleteStates.confirm, F.data == "dhxn")
async def hwd_confirm_no(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    try:
        await query.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    await query.message.answer("Удаление отменено.")
    await query.answer()


@router.message(HomeworkDeleteStates.confirm, F.text == "Меню")
async def hwd_confirm_menu(
    message: Message, state: FSMContext, is_elder: bool, db_user: User | None
) -> None:
    from handlers.common import cmd_menu

    await cmd_menu(message, is_elder, db_user, state)


@router.message(HomeworkDeleteStates.confirm, F.text)
async def hwd_confirm_noise(message: Message) -> None:
    await message.answer("Подтверди удаление кнопками под предыдущим сообщением.")


@router.message(HomeworkDeleteStates.pick_date)
async def hwd_pick_date_noise(message: Message) -> None:
    await message.answer(
        "Выбери дату кнопкой «Сегодня» / «Завтра» или введи дату текстом (ДД.ММ).",
    )
