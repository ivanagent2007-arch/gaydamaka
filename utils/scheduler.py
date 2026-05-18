from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

import config
from database import (
    BirthdayReminderSent,
    Deadline,
    StudyGroup,
    User,
    UserRole,
    async_session_maker,
)
from utils.attendance_report import attendance_group_day_stats, build_attendance_report_text
from utils.birthday_helpers import format_birthday_display, next_birthday
from utils.group_mail_worker import poll_one_group_mail

logger = logging.getLogger(__name__)


def _now_tz() -> datetime:
    return datetime.now(ZoneInfo(config.TZ))


def _group_elders(members: list[User]) -> list[User]:
    seen: set[int] = set()
    out: list[User] = []
    for u in members:
        if u.id in seen:
            continue
        if u.role in (UserRole.elder, UserRole.deputy_elder) or config.is_elder(
            u.telegram_id, None
        ):
            seen.add(u.id)
            out.append(u)
    return out


async def notify_elders_if_attendance_complete(
    bot: Bot, session, study_group: StudyGroup, report_date: date
) -> None:
    """Шлёт старостам отчёт за день, когда все студенты отметились по всем парам (без ежедневного cron)."""
    data = await attendance_group_day_stats(session, study_group, report_date)
    if data["lesson_slots_count"] == 0 or data["students_total"] == 0:
        return
    if data["students_with_any_mark_count"] == 0:
        return
    if not data["all_students_fully_marked"]:
        return
    text = await build_attendance_report_text(session, study_group, report_date)
    members = (
        await session.scalars(
            select(User).where(User.study_group_id == study_group.id)
        )
    ).all()
    elders = _group_elders(list(members))
    for e in elders:
        try:
            await bot.send_message(e.telegram_id, text)
        except Exception as ex:
            logger.exception(ex)


def _deadline_as_local(dt: datetime) -> datetime:
    tz = ZoneInfo(config.TZ)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


async def send_birthday_reminders(bot: Bot) -> None:
    today = _now_tz().date()
    async with async_session_maker() as session:
        q = await session.scalars(
            select(User).where(
                User.birthday_month.isnot(None),
                User.birthday_day.isnot(None),
                User.study_group_id.isnot(None),
            )
        )
        celebrants = list(q)
        for c in celebrants:
            assert c.birthday_month and c.birthday_day
            next_d = next_birthday(c.birthday_month, c.birthday_day, today)
            delta = (next_d - today).days
            if delta not in (0, 1, 7, 14):
                continue
            kind_map = {0: "0d", 1: "1d", 7: "1w", 14: "2w"}
            kind = kind_map[delta]
            year_ev = next_d.year
            exists = await session.scalar(
                select(BirthdayReminderSent).where(
                    BirthdayReminderSent.celebrant_user_id == c.id,
                    BirthdayReminderSent.event_year == year_ev,
                    BirthdayReminderSent.kind == kind,
                )
            )
            if exists:
                continue
            ds = format_birthday_display(
                c.birthday_day, c.birthday_month, c.birth_year
            )
            if delta == 14:
                msg = f"🎂 Через 2 недели день рождения у {c.full_name} ({ds})."
            elif delta == 7:
                msg = f"🎂 Через неделю день рождения у {c.full_name} ({ds})."
            elif delta == 1:
                msg = f"🎂 Завтра день рождения у {c.full_name} ({ds})."
            else:
                msg = f"🎂 Сегодня день рождения у {c.full_name}! С праздником!"
            members = (
                await session.scalars(
                    select(User).where(User.study_group_id == c.study_group_id)
                )
            ).all()
            if delta == 0:
                targets = members
            else:
                targets = [u for u in members if u.role == UserRole.student]
            for m in targets:
                try:
                    await bot.send_message(m.telegram_id, msg)
                except Exception as ex:
                    logger.debug("birthday notify %s: %s", m.telegram_id, ex)
            # Коммит на каждого именинника: метка «уже отправлено» сохраняется
            # сразу после рассылки, чтобы крах процесса позже не привёл к повтору.
            session.add(
                BirthdayReminderSent(
                    celebrant_user_id=c.id,
                    event_year=year_ev,
                    kind=kind,
                )
            )
            await session.commit()


async def notify_deadlines_24h(bot: Bot) -> None:
    now = _now_tz()
    async with async_session_maker() as session:
        q = await session.scalars(
            select(Deadline).where(Deadline.notified_24h.is_(False))
        )
        deadlines = []
        for d in q:
            dl = _deadline_as_local(d.deadline_date)
            delta = dl - now
            if timedelta(hours=22) <= delta <= timedelta(hours=26):
                deadlines.append(d)
        for d in deadlines:
            if not d.study_group_id:
                continue
            users = (
                await session.scalars(
                    select(User).where(User.study_group_id == d.study_group_id)
                )
            ).all()
            subj = f" ({d.subject})" if d.subject else ""
            msg = (
                f"Напоминание: через ~24 часа дедлайн{subj}\n"
                f"<b>{d.title}</b>\n{d.description[:500]}"
            )
            for u in users:
                try:
                    await bot.send_message(u.telegram_id, msg, parse_mode="HTML")
                except Exception as ex:
                    logger.debug("deadline notify skip %s: %s", u.telegram_id, ex)
            # Коммит на каждый дедлайн отдельно: если процесс упадёт после рассылки
            # одного дедлайна, флаг по нему уже сохранён, а следующий запуск не
            # отправит дубликат тем же пользователям.
            d.notified_24h = True
            await session.commit()


async def poll_group_mail(bot: Bot) -> None:
    async with async_session_maker() as session:
        q = await session.scalars(
            select(StudyGroup).where(
                StudyGroup.corporate_email.isnot(None),
                StudyGroup.imap_password.isnot(None),
                StudyGroup.corporate_email != "",
                StudyGroup.imap_password != "",
            )
        )
        groups = list(q)
        for sg in groups:
            try:
                _, err = await poll_one_group_mail(bot, session, sg)
                if err:
                    await session.rollback()
                else:
                    await session.commit()
            except Exception:
                await session.rollback()
                raise


async def refresh_all_schedules() -> None:
    """Фоновая сверка расписания каждой группы с РУЗ: подтягивает смены аудиторий, отмены,
    замены преподавателей без участия старосты. Окно — узкая окрестность сегодня
    (RUZ_SCHEDULE_REFRESH_PAST_DAYS … FUTURE_DAYS), чтобы один проход был быстрым.

    На каждую группу — жёсткий таймаут (RUZ_SCHEDULE_REFRESH_GROUP_TIMEOUT_S, по умолчанию
    120 сек). Без него одна зависшая группа в РУЗ заблокировала бы весь job (а APScheduler
    с max_instances=1 пропустил бы все последующие циклы — расписание не обновлялось бы).
    """
    import asyncio as _asyncio

    from handlers.schedule import sync_study_group_schedule_for_range  # ленивый импорт

    today = date.today()
    rng_lo = today - timedelta(days=config.RUZ_SCHEDULE_REFRESH_PAST_DAYS)
    rng_hi = today + timedelta(days=config.RUZ_SCHEDULE_REFRESH_FUTURE_DAYS)
    per_group_timeout = float(
        getattr(config, "RUZ_SCHEDULE_REFRESH_GROUP_TIMEOUT_S", 120)
    )
    async with async_session_maker() as session:
        group_ids = list(
            await session.scalars(
                select(StudyGroup.id).where(
                    StudyGroup.ruz_group_search.isnot(None),
                    StudyGroup.ruz_group_search != "",
                )
            )
        )
    for gid in group_ids:
        async with async_session_maker() as session:
            try:
                sg = await session.get(StudyGroup, gid)
                if sg is None:
                    continue
                await _asyncio.wait_for(
                    sync_study_group_schedule_for_range(session, sg, rng_lo, rng_hi),
                    timeout=per_group_timeout,
                )
                await session.commit()
            except _asyncio.TimeoutError:
                await session.rollback()
                logger.warning(
                    "refresh_all_schedules: РУЗ не ответил за %ss, group_id=%s — пропускаем до следующего цикла",
                    per_group_timeout, gid,
                )
            except Exception:
                await session.rollback()
                logger.exception("refresh_all_schedules: group_id=%s", gid)


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    sched = AsyncIOScheduler(timezone=ZoneInfo(config.TZ))
    sched.add_job(
        notify_deadlines_24h,
        "interval",
        minutes=15,
        args=[bot],
        id="deadlines_24h",
        replace_existing=True,
    )
    sched.add_job(
        send_birthday_reminders,
        "cron",
        hour=9,
        minute=0,
        args=[bot],
        id="birthday_reminders",
        replace_existing=True,
    )
    sched.add_job(
        poll_group_mail,
        "interval",
        minutes=5,
        args=[bot],
        id="poll_group_mail",
        replace_existing=True,
    )
    # Долгая сетевая работа (РУЗ-API на каждую группу) — не даём ей перекрываться сама с собой.
    # next_run_time: первый прогон через 2 минуты после старта, чтобы не толкаться с другими job'ами.
    sched.add_job(
        refresh_all_schedules,
        "interval",
        hours=config.RUZ_SCHEDULE_REFRESH_HOURS,
        id="ruz_schedule_refresh",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        next_run_time=_now_tz() + timedelta(minutes=2),
    )
    return sched
