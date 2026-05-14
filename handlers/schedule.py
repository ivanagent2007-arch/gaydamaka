from __future__ import annotations

import html
from datetime import date, datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import default_state
from aiogram.types import Message
from sqlalchemy import delete, func, select

import config
from database import Schedule, StudyGroup, User
from handlers.states import SchedulePickDateStates
from keyboards.reply import schedule_submenu_kb
from utils.group_context import get_study_group, ruz_base_url_for_group, ruz_search_for_group
from utils.parser import fetch_schedule_async, fetch_schedule_html_fallback
from utils.schedule_subjects import distinct_schedule_subjects
from utils.semester_sync import sync_group_semester_catalog
from utils.user_roles import effective_is_elder

router = Router(name="schedule")


def _esc(s: str) -> str:
    return html.escape(s or "", quote=False)


# Одна строка-разделитель между парами (узкий блок Unicode)
_LESSON_SEP = "────────────────────────"


def _parse_start_minutes(st: str | None) -> int:
    raw = (st or "").strip().split(":")[:3]
    if len(raw) >= 2:
        try:
            return int(raw[0]) * 60 + int(raw[1])
        except ValueError:
            pass
    return 10**9


def _slot_order_map_from_week(week_rows: list[Schedule]) -> dict[str, int]:
    """Уникальные времена начала за неделю, по порядку времени — официальный индекс пары для UI."""
    starts = sorted(
        {(s.start_time or "").strip() for s in week_rows if (s.start_time or "").strip()},
        key=lambda t: (_parse_start_minutes(t), t),
    )
    return {st: i + 1 for i, st in enumerate(starts)}


def _lesson_slot_indices(day_rows: list[Schedule], week_rows: list[Schedule]) -> list[int]:
    """Один номер слота на одно время начала; номер считается по месту времени среди всей загруженной недели."""
    order = _slot_order_map_from_week(week_rows)
    nums: list[int] = []
    fb_slot = len(order)
    fb_prev: str | None = None
    for s in day_rows:
        st = (s.start_time or "").strip()
        if st and st in order:
            nums.append(order[st])
            fb_prev = None
            continue
        if st != fb_prev:
            fb_slot += 1
            fb_prev = st
        nums.append(fb_slot)
    return nums


def _lesson_visual_merge_key(s: Schedule) -> str:
    """Соседние строки с одним предметом, типом и временем начала — одна цепочка; номер слота только у первой."""
    return (
        (s.subject or "").strip().casefold()
        + "|"
        + (s.lesson_kind or "").strip().casefold()
        + "|"
        + (s.start_time or "").strip()
    )


def _all_lesson_kinds_empty(rows: list[Schedule]) -> bool:
    """True, если в кэше ещё нет типов занятий (лекция/семинар) — нужна подгрузка с РУЗ."""
    if not rows:
        return False
    return all(not (r.lesson_kind or "").strip() for r in rows)


def _any_contingent_label_missing(rows: list[Schedule]) -> bool:
    """True, если у хотя бы одной пары нет строки группы/потока (старый кэш до поля contingent_label)."""
    if not rows:
        return False
    return any(not (r.contingent_label or "").strip() for r in rows)


def _fmt_lesson_time_line(s: Schedule) -> str:
    teach = _esc((s.teacher or "").strip()) or "—"
    room = _esc((s.room or "").strip()) or "—"
    t0 = _esc(s.start_time)
    t1 = _esc(s.end_time)
    return f"<b>{t0}–{t1}</b> · <b>{teach}</b> · <b>{room}</b>"


def _fmt_contingent_line(s: Schedule) -> str:
    c = (s.contingent_label or "").strip()
    if not c:
        return ""
    return f"\n🎓 <i>{_esc(c)}</i>"


def _fmt_lesson_first(idx: int, s: Schedule) -> str:
    subj = _esc(s.subject)
    kind = (s.lesson_kind or "").strip()
    kind_line = f" <i>({_esc(kind)})</i>" if kind else ""
    cont = _fmt_contingent_line(s)
    return f"<b>{idx}. {subj}</b>{kind_line}{cont}\n{_fmt_lesson_time_line(s)}"


def _format_lesson_rows(rows: list[Schedule], slot_nums: list[int]) -> str:
    """Подряд идущие пары с одним предметом, типом и временем начала — без разделителя; номер только у первой."""
    parts: list[str] = []
    prev_key: str | None = None
    for i, s in enumerate(rows):
        key = _lesson_visual_merge_key(s)
        is_cont = prev_key is not None and key == prev_key
        if i > 0:
            if is_cont:
                parts.append("\n")
            else:
                parts.append(f"\n{_LESSON_SEP}\n")
        if is_cont:
            parts.append(_fmt_lesson_time_line(s))
        else:
            parts.append(_fmt_lesson_first(slot_nums[i], s))
        prev_key = key
    return "".join(parts)


def _fmt_day_header(sg_name: str, day: date, title: str) -> str:
    wd = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")[day.weekday()]
    ds = day.strftime("%d.%m.%Y")
    return f"<b>{_esc(sg_name)}</b>\n{title}: {wd}, {ds}"


async def sync_study_group_schedule_for_range(
    session,
    sg: StudyGroup,
    rng_lo: date,
    rng_hi: date,
) -> int:
    """Сверяет [rng_lo, rng_hi] с РУЗ через UPSERT: совпавшие по ключу строки обновляются,
    новые добавляются, пропавшие — удаляются. Schedule.id сохраняется для неизменных уроков,
    чтобы Attendance.schedule_id (ON DELETE CASCADE) не терял посещаемость при пересинке."""
    if rng_lo > rng_hi:
        rng_lo, rng_hi = rng_hi, rng_lo
    ruz_q = ruz_search_for_group(sg)
    ruz_url = ruz_base_url_for_group(sg)
    rows = await fetch_schedule_async(
        sg.name,
        ruz_q,
        base_url=ruz_url,
        date_begin=rng_lo,
        date_end=rng_hi,
    )
    used_fallback = False
    if not rows:
        today = date.today()
        if rng_lo <= today <= rng_hi:
            rows = fetch_schedule_html_fallback(sg.name, base_url=ruz_url)
            used_fallback = bool(rows)
    if not rows:
        return 0

    seen: set[tuple[date, str, str, str, str, str]] = set()
    deduped: list[dict] = []
    for r in rows:
        tch = (r.get("teacher") or "").strip()
        lk = (r.get("lesson_kind") or "").strip()
        cont = (r.get("contingent_label") or "").strip()
        key = (r["lesson_date"], r["start_time"], r["subject"], tch, lk, cont)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)

    existing_rows = list(
        await session.scalars(
            select(Schedule).where(
                Schedule.study_group_id == sg.id,
                Schedule.lesson_date >= rng_lo,
                Schedule.lesson_date <= rng_hi,
            )
        )
    )
    existing_by_key: dict[tuple, Schedule] = {}
    for s in existing_rows:
        ekey = (
            s.lesson_date,
            (s.start_time or ""),
            s.subject or "",
            s.teacher or "",
            s.lesson_kind or "",
            s.contingent_label or "",
        )
        existing_by_key[ekey] = s

    new_keys: set[tuple] = set()
    for r in deduped:
        key = (
            r["lesson_date"],
            r["start_time"],
            r["subject"],
            (r.get("teacher") or ""),
            (r.get("lesson_kind") or ""),
            (r.get("contingent_label") or ""),
        )
        new_keys.add(key)
        existing = existing_by_key.get(key)
        if existing is not None:
            existing.group_name = sg.name
            existing.day_of_week = r["day_of_week"]
            existing.lesson_number = r["lesson_number"]
            existing.room = r.get("room") or ""
            existing.end_time = r["end_time"]
        else:
            session.add(
                Schedule(
                    study_group_id=sg.id,
                    group_name=sg.name,
                    lesson_date=r["lesson_date"],
                    day_of_week=r["day_of_week"],
                    lesson_number=r["lesson_number"],
                    subject=r["subject"],
                    teacher=r.get("teacher") or "",
                    room=r.get("room") or "",
                    start_time=r["start_time"],
                    end_time=r["end_time"],
                    lesson_kind=r.get("lesson_kind") or "",
                    contingent_label=r.get("contingent_label") or "",
                )
            )

    if not used_fallback:
        # HTML fallback заведомо неполный (только сегодня) — не удаляем по нему.
        stale_ids = [s.id for k, s in existing_by_key.items() if k not in new_keys]
        if stale_ids:
            await session.execute(
                delete(Schedule).where(Schedule.id.in_(stale_ids))
            )

    await session.flush()
    subs = await distinct_schedule_subjects(session, sg.id)
    await sync_group_semester_catalog(session, sg, subs)
    return len(deduped)


async def sync_study_group_schedule(session, sg: StudyGroup) -> int:
    """Полная подгрузка по настройкам RUZ_SCHEDULE_* (староста /update_schedule и т.п.)."""
    today = date.today()
    rng_lo = today - timedelta(days=config.RUZ_SCHEDULE_PAST_DAYS)
    rng_hi = today + timedelta(days=config.RUZ_SCHEDULE_FUTURE_DAYS)
    return await sync_study_group_schedule_for_range(session, sg, rng_lo, rng_hi)


async def schedule_lesson_date_bounds(
    session, study_group_id: int
) -> tuple[date | None, date | None]:
    row = await session.execute(
        select(func.min(Schedule.lesson_date), func.max(Schedule.lesson_date)).where(
            Schedule.study_group_id == study_group_id,
        )
    )
    return row.one()


async def schedule_cache_needs_expansion(
    session, study_group_id: int, range_start: date, range_end: date
) -> bool:
    mn, mx = await schedule_lesson_date_bounds(session, study_group_id)
    if mn is None or mx is None:
        return True
    return bool(range_start < mn or range_end > mx)


async def ensure_schedule_cache_covers_range(
    session, sg: StudyGroup, range_start: date, range_end: date
) -> bool:
    """Догружает с РУЗ только окрестность запроса (быстро для Web App и бота при листании)."""
    if not await schedule_cache_needs_expansion(session, sg.id, range_start, range_end):
        return False
    pad = timedelta(days=21)
    today = date.today()
    abs_lo = today - timedelta(days=config.RUZ_SCHEDULE_PAST_DAYS)
    abs_hi = today + timedelta(days=config.RUZ_SCHEDULE_FUTURE_DAYS)
    lo = max(abs_lo, range_start - pad)
    hi = min(abs_hi, range_end + pad)
    if lo > hi:
        return False
    n = await sync_study_group_schedule_for_range(session, sg, lo, hi)
    return n > 0


async def _lessons_for_day(
    session, study_group_id: int, day: date
) -> list[Schedule]:
    q = await session.scalars(
        select(Schedule)
        .where(
            Schedule.study_group_id == study_group_id,
            Schedule.lesson_date == day,
        )
        .order_by(Schedule.start_time)
    )
    return list(q)


async def _lessons_for_week_of(
    session, study_group_id: int, any_day: date
) -> list[Schedule]:
    week_start = any_day - timedelta(days=any_day.weekday())
    week_end = week_start + timedelta(days=6)
    q = await session.scalars(
        select(Schedule)
        .where(
            Schedule.study_group_id == study_group_id,
            Schedule.lesson_date >= week_start,
            Schedule.lesson_date <= week_end,
        )
        .order_by(Schedule.lesson_date, Schedule.start_time)
    )
    return list(q)


async def send_schedule_day(
    message: Message,
    session,
    db_user: User | None,
    day: date,
    title_ru: str,
) -> None:
    if not db_user or not db_user.study_group_id:
        await message.answer(
            "Сначала вступи в группу: введи код старосты (/groups или «Ввести код группы»)."
        )
        return
    sg = await get_study_group(session, db_user)
    if not sg:
        await message.answer("Группа не найдена. Выбери снова: /groups")
        return

    today = date.today()
    rows = await _lessons_for_day(session, db_user.study_group_id, day)

    need_bulk = await schedule_cache_needs_expansion(
        session, db_user.study_group_id, day, day
    )
    if not rows and need_bulk:
        await message.answer(f"Группа «{sg.name}». Загружаю расписание с сайта РУЗ…")
    if await ensure_schedule_cache_covers_range(session, sg, day, day):
        rows = await _lessons_for_day(session, db_user.study_group_id, day)
    elif not rows and need_bulk:
        await message.answer(
            "Не удалось загрузить расписание с сайта. "
            "Пусть староста проверит в /my_group строку поиска РУЗ "
            "или выполнит /update_schedule."
        )
        return

    if rows and day >= today and (
        _all_lesson_kinds_empty(rows) or _any_contingent_label_missing(rows)
    ):
        n = await sync_study_group_schedule(session, sg)
        if n:
            rows = await _lessons_for_day(session, db_user.study_group_id, day)

    if not rows:
        hdr = _fmt_day_header(sg.name, day, title_ru)
        await message.answer(
            f"{hdr}\n\n<i>Пар нет (по данным РУЗ).</i>",
            parse_mode="HTML",
        )
        return

    hdr = _fmt_day_header(sg.name, day, title_ru)
    week_rows = await _lessons_for_week_of(session, db_user.study_group_id, day)
    slot_nums = _lesson_slot_indices(rows, week_rows)
    body = _format_lesson_rows(rows, slot_nums)
    await message.answer(f"{hdr}\n\n{body}", parse_mode="HTML")


async def send_schedule_week(message: Message, session, db_user: User | None) -> None:
    if not db_user or not db_user.study_group_id:
        await message.answer(
            "Сначала вступи в группу: введи код старосты (/groups или «Ввести код группы»)."
        )
        return
    sg = await get_study_group(session, db_user)
    if not sg:
        await message.answer("Группа не найдена. Выбери снова: /groups")
        return

    today = date.today()
    end = today + timedelta(days=6)

    need_bulk = await schedule_cache_needs_expansion(session, db_user.study_group_id, today, end)
    if need_bulk:
        await message.answer(f"Группа «{sg.name}». Загружаю расписание с сайта РУЗ…")
    if not await ensure_schedule_cache_covers_range(session, sg, today, end) and need_bulk:
        await message.answer(
            "Не удалось загрузить расписание с сайта. "
            "Пусть староста проверит строку РУЗ в /my_group или выполнит /update_schedule."
        )
        return

    q = await session.scalars(
        select(Schedule)
        .where(
            Schedule.study_group_id == db_user.study_group_id,
            Schedule.lesson_date >= today,
            Schedule.lesson_date <= end,
        )
        .order_by(Schedule.lesson_date, Schedule.start_time)
    )
    rows = list(q)
    if rows and (
        _all_lesson_kinds_empty(rows) or _any_contingent_label_missing(rows)
    ):
        n = await sync_study_group_schedule(session, sg)
        if n:
            q = await session.scalars(
                select(Schedule)
                .where(
                    Schedule.study_group_id == db_user.study_group_id,
                    Schedule.lesson_date >= today,
                    Schedule.lesson_date <= end,
                )
                .order_by(Schedule.lesson_date, Schedule.start_time)
            )
            rows = list(q)
    if not rows:
        await message.answer(
            f"<b>{_esc(sg.name)}</b>\n7 дней с сегодня\n\n"
            f"<i>Занятий нет (по данным РУЗ).</i>",
            parse_mode="HTML",
        )
        return

    by_day: dict[date, list[Schedule]] = {}
    for s in rows:
        d = s.lesson_date
        if d is None:
            continue
        by_day.setdefault(d, []).append(s)

    parts: list[str] = [f"<b>{_esc(sg.name)}</b>\n7 дней с сегодня"]
    for d in sorted(by_day.keys()):
        wd = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")[d.weekday()]
        ds = d.strftime("%d.%m.%Y")
        parts.append(f"\n<b>{wd} {ds}</b>")
        day_rows = by_day[d]
        slot_nums = _lesson_slot_indices(day_rows, rows)
        parts.append("\n" + _format_lesson_rows(day_rows, slot_nums))
    text = "\n".join(parts)
    await message.answer(text, parse_mode="HTML")


async def send_today_schedule(
    message: Message, session, db_user: User | None
) -> None:
    await send_schedule_day(message, session, db_user, date.today(), "Сегодня")


def _parse_ddmmyyyy(raw: str) -> date | None:
    raw = (raw or "").strip()
    for fmt in ("%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


@router.message(SchedulePickDateStates.waiting_date, F.text == "Сегодня")
async def schedule_waiting_today(
    message: Message, state: FSMContext, session, db_user: User | None
) -> None:
    if not db_user or not db_user.study_group_id:
        await state.clear()
        await message.answer("Сначала /start и вступи в группу.")
        return
    await state.clear()
    await send_schedule_day(message, session, db_user, date.today(), "Сегодня")


@router.message(SchedulePickDateStates.waiting_date, F.text == "Завтра")
async def schedule_waiting_tomorrow(
    message: Message, state: FSMContext, session, db_user: User | None
) -> None:
    if not db_user or not db_user.study_group_id:
        await state.clear()
        await message.answer("Сначала /start и вступи в группу.")
        return
    await state.clear()
    await send_schedule_day(
        message, session, db_user, date.today() + timedelta(days=1), "Завтра"
    )


@router.message(SchedulePickDateStates.waiting_date, F.text == "Неделя")
async def schedule_waiting_week(
    message: Message, state: FSMContext, session, db_user: User | None
) -> None:
    if not db_user or not db_user.study_group_id:
        await state.clear()
        await message.answer("Сначала /start и вступи в группу.")
        return
    await state.clear()
    await send_schedule_week(message, session, db_user)


@router.message(SchedulePickDateStates.waiting_date, F.text == "Дата")
async def schedule_waiting_date_again(message: Message, db_user: User | None) -> None:
    if not db_user or not db_user.study_group_id:
        await message.answer("Сначала /start и вступи в группу.")
        return
    await message.answer(
        "Дата: <b>ДД.ММ.ГГГГ</b>, пример <code>15.09.2025</code>.",
        parse_mode="HTML",
    )


@router.message(SchedulePickDateStates.waiting_date)
async def schedule_date_entered(message: Message, state: FSMContext, session, db_user: User | None) -> None:
    if not db_user or not db_user.study_group_id:
        await state.clear()
        await message.answer("Сначала /start и вступи в группу.")
        return
    d = _parse_ddmmyyyy(message.text or "")
    if not d:
        await message.answer(
            "Не понял дату. Пример: <code>24.03.2026</code>. Повтори или «Меню».",
            parse_mode="HTML",
        )
        return
    await state.clear()
    await send_schedule_day(message, session, db_user, d, "Расписание на")


@router.message(Command("schedule"))
async def cmd_schedule(message: Message, session, db_user: User | None) -> None:
    await send_today_schedule(message, session, db_user)


@router.message(Command("schedule_week"))
async def cmd_schedule_week_cmd(message: Message, session, db_user: User | None) -> None:
    await send_schedule_week(message, session, db_user)


@router.message(F.text == "Расписание", StateFilter(default_state))
async def text_schedule_menu(message: Message, session, db_user: User | None) -> None:
    if not db_user or not db_user.study_group_id:
        await message.answer(
            "Сначала вступи в группу: введи код старосты (/groups или «Ввести код группы»)."
        )
        return
    sg = await get_study_group(session, db_user)
    name = sg.name if sg else (db_user.group_name or "").strip() or "группа"
    await message.answer(
        f"<b>{_esc(name)}</b>\nУ каждой пары в расписании указаны поток и подгруппа из РУЗ, "
        f"если они есть.\n\nВыбери период:",
        parse_mode="HTML",
        reply_markup=schedule_submenu_kb(),
    )


@router.message(F.text == "Сегодня", StateFilter(default_state))
async def text_today(message: Message, session, db_user: User | None) -> None:
    if not db_user or not db_user.study_group_id:
        await message.answer(
            "Сначала вступи в группу: введи код старосты (/groups или «Ввести код группы»)."
        )
        return
    await send_schedule_day(message, session, db_user, date.today(), "Сегодня")


@router.message(F.text == "Завтра", StateFilter(default_state))
async def text_tomorrow(message: Message, session, db_user: User | None) -> None:
    if not db_user or not db_user.study_group_id:
        await message.answer(
            "Сначала вступи в группу: введи код старосты (/groups или «Ввести код группы»)."
        )
        return
    await send_schedule_day(
        message, session, db_user, date.today() + timedelta(days=1), "Завтра"
    )


@router.message(F.text == "Неделя", StateFilter(default_state))
async def text_week(message: Message, session, db_user: User | None) -> None:
    if not db_user or not db_user.study_group_id:
        await message.answer(
            "Сначала вступи в группу: введи код старосты (/groups или «Ввести код группы»)."
        )
        return
    await send_schedule_week(message, session, db_user)


@router.message(F.text == "Дата", StateFilter(default_state))
async def text_pick_date(message: Message, state: FSMContext, db_user: User | None) -> None:
    if not db_user or not db_user.study_group_id:
        await message.answer(
            "Сначала вступи в группу: введи код старосты (/groups или «Ввести код группы»)."
        )
        return
    await state.set_state(SchedulePickDateStates.waiting_date)
    await message.answer(
        "Дата: <b>ДД.ММ.ГГГГ</b>, пример <code>15.09.2025</code>.",
        parse_mode="HTML",
    )


@router.message(Command("update_schedule"))
async def cmd_update_schedule(message: Message, session, db_user: User | None) -> None:
    fu = message.from_user
    if fu is None or not effective_is_elder(db_user, fu.id, fu.username):
        await message.answer("Только староста может обновлять расписание.")
        return
    sg = await get_study_group(session, db_user)
    if not sg:
        await message.answer("Сначала создай группу (/create_group) или вступи в неё.")
        return
    await message.answer("Качаю расписание из РУЗ…")
    n = await sync_study_group_schedule(session, sg)
    if not n:
        await message.answer(
            "Не удалось получить расписание. Проверь при создании группы строку "
            "поиска РУЗ и интернет."
        )
        return
    await message.answer(f"Готово. Загружено занятий: {n}.")
