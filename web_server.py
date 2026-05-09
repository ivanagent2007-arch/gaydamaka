from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

from aiohttp import web
from sqlalchemy import or_, select
import config
from database import (
    Attendance,
    Deadline,
    Grade,
    GroupEmailAttachment,
    GroupEmailMailbox,
    GroupEmailMessage,
    Homework,
    SantaGame,
    SantaPair,
    Schedule,
    SiteGrade,
    StudyGroup,
    User,
    UserRole,
    async_session_maker,
)
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
from utils.attendance_report import attendance_group_day_stats
from utils.group_roster import (
    elder_remove_student,
    get_group_member_rows,
    set_group_deputy,
)
from utils.homework_deadline import deadline_for_homework
from utils.group_email_attachments_store import (
    ascii_download_filename,
    is_safe_attachment_stored_path,
)
from utils.homework_delete import delete_homework_for_study_group
from utils.webapp import init_data_from_request_headers, validate_init_data

from handlers.schedule import (
    ensure_schedule_cache_covers_range,
    schedule_cache_needs_expansion,
)

logger = logging.getLogger(__name__)


def _schedule_rows_need_ruz_refresh(schedules: list[Schedule]) -> bool:
    """Старый кэш: нет типа занятия или нет строки группы/потока — перекачать с РУЗ."""
    if not schedules:
        return False
    if all(not (s.lesson_kind or "").strip() for s in schedules):
        return True
    if any(not (s.contingent_label or "").strip() for s in schedules):
        return True
    return False


async def _resync_schedule_cache_if_stale(
    session,
    sg: StudyGroup | None,
    schedules: list[Schedule],
) -> bool:
    """Перезаписать расписание из РУЗ, если кэш без lesson_kind или contingent. Возвращает True, если был commit."""
    if not sg or not schedules or not _schedule_rows_need_ruz_refresh(schedules):
        return False
    today = date.today()
    if all(s.lesson_date is not None and s.lesson_date < today for s in schedules):
        return False
    from handlers.schedule import sync_study_group_schedule

    n = await sync_study_group_schedule(session, sg)
    if not n:
        return False
    await session.commit()
    return True


def _site_marks_display(marks_json: str | None) -> str:
    if not marks_json:
        return ""
    try:
        marks = json.loads(marks_json)
    except (json.JSONDecodeError, TypeError):
        return ""
    if not marks:
        return ""
    parts = [f"{m['type']}={int(m['val'])}" for m in marks if m.get("val") is not None]
    return ", ".join(parts)


def _path_is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _relpath_under_base_for_api_file(dest: Path) -> str:
    """Путь для /api/file: только внутри каталога проекта (BASE_DIR)."""
    fr = dest.resolve()
    br = config.BASE_DIR.resolve()
    try:
        return str(fr.relative_to(br)).replace("\\", "/")
    except ValueError:
        logger.error(
            "UPLOAD_DIR вне BASE_DIR: файл=%s, BASE_DIR=%s, UPLOAD_DIR=%s",
            fr,
            br,
            config.UPLOAD_DIR.resolve(),
        )
        raise web.HTTPInternalServerError(
            text="UPLOAD_DIR в .env должен быть внутри каталога бота (например data/uploads)."
        ) from None


def _allowed_homework_upload_roots() -> list[Path]:
    br = config.BASE_DIR.resolve()
    roots = [config.UPLOAD_DIR.resolve(), (br / "data" / "uploads").resolve()]
    out: list[Path] = []
    for r in roots:
        if r not in out:
            out.append(r)
    return out


def _validate_homework_file_rel(path_str: str) -> str | None:
    rel = path_str.strip().replace("\\", "/")
    if not rel or ".." in rel or rel.startswith("/"):
        return None
    full = (config.BASE_DIR / rel).resolve()
    if not _path_is_under(full, config.BASE_DIR.resolve()):
        return None
    if not any(_path_is_under(full, root) for root in _allowed_homework_upload_roots()):
        return None
    if not full.is_file():
        return None
    return rel.replace("\\", "/")


def _homework_web_upload_dir() -> Path:
    """Каталог для загрузки из мини-приложения: UPLOAD_DIR, если внутри проекта, иначе data/uploads."""
    br = config.BASE_DIR.resolve()
    ur = config.UPLOAD_DIR.resolve()
    if _path_is_under(ur, br):
        return ur
    fallback = br / "data" / "uploads"
    logger.warning(
        "UPLOAD_DIR (%s) вне BASE_DIR (%s) — веб-загрузка ДЗ в %s",
        ur,
        br,
        fallback,
    )
    return fallback


def _monday_week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _parse_iso_date_query(s: str | None) -> date | None:
    if not s:
        return None
    raw = str(s).strip()[:10]
    if len(raw) != 10:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _webapp_dir() -> Path:
    return Path(__file__).resolve().parent / "web_app"


async def _auth_user(request: web.Request) -> dict:
    raw = init_data_from_request_headers(request.headers)
    if not raw and request.content_type == "application/json":
        try:
            body = await request.json()
            raw = body.get("initData") or ""
        except (json.JSONDecodeError, TypeError, ValueError):
            raw = ""
    if not raw:
        raise web.HTTPUnauthorized(text="no init data")
    try:
        data = validate_init_data(raw)
    except ValueError as e:
        raise web.HTTPUnauthorized(text=str(e)) from e
    tid = data["telegram_id"]
    async with async_session_maker() as session:
        user = await session.scalar(select(User).where(User.telegram_id == tid))
        if not user:
            raise web.HTTPForbidden(text="not registered, use /start in bot")
        return {
            "id": user.id,
            "telegram_id": user.telegram_id,
            "full_name": user.full_name,
            "group_name": user.group_name,
            "study_group_id": user.study_group_id,
            "role": user.role.value,
        }


def _has_elder_privileges(u: dict) -> bool:
    """Староста, заместитель или ID из .env — доступ к баллам, ДЗ, дедлайнам и т.д."""
    if u["role"] == UserRole.elder.value:
        return True
    if u["role"] == UserRole.deputy_elder.value:
        return True
    return config.is_elder(u["telegram_id"], None)


def _is_chief_elder_web(u: dict) -> bool:
    """Только староста (не зам): исключение, назначение зама."""
    if u["role"] == UserRole.elder.value:
        return True
    return config.is_elder(u["telegram_id"], None)


def _parse_deadline_datetime_naive(raw: str) -> datetime | None:
    """Наивный локальный момент дедлайна (как в /set_deadline), TZ из .env только если в строке есть смещение."""
    s = (raw or "").strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        dt = None
    if dt is None:
        for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y %H.%M"):
            try:
                return datetime.strptime(s[:16], fmt)
            except ValueError:
                continue
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(ZoneInfo(config.TZ)).replace(tzinfo=None)
    return dt


async def api_me(request: web.Request) -> web.Response:
    u = await _auth_user(request)
    return web.json_response(
        {
            "telegram_id": u["telegram_id"],
            "full_name": u["full_name"],
            "group_name": u["group_name"],
            "study_group_id": u["study_group_id"],
            "role": u["role"],
            "db_user_id": u["id"],
            "is_elder": _has_elder_privileges(u),
            "is_chief_elder": _is_chief_elder_web(u),
            "is_deputy": u["role"] == UserRole.deputy_elder.value,
        }
    )


async def api_group_mail(request: web.Request) -> web.Response:
    u = await _auth_user(request)
    if not u["study_group_id"]:
        return web.json_response({"items": [], "need_group": True})
    async with async_session_maker() as session:
        stmt = (
            select(GroupEmailMessage, GroupEmailMailbox.title)
            .outerjoin(
                GroupEmailMailbox,
                GroupEmailMessage.mailbox_id == GroupEmailMailbox.id,
            )
            .where(GroupEmailMessage.study_group_id == u["study_group_id"])
            .order_by(GroupEmailMessage.received_at.desc())
            .limit(80)
        )
        res = await session.execute(stmt)
        items = []
        for msg, mb_title in res:
            preview = (msg.body_preview or "")[:220]
            items.append(
                {
                    "id": msg.id,
                    "sender": msg.sender or "",
                    "subject": msg.subject or "",
                    "received_at": (
                        msg.received_at.isoformat() if msg.received_at else None
                    ),
                    "mailbox_title": mb_title,
                    "snippet": preview,
                }
            )
    return web.json_response({"items": items})


async def api_group_mail_one(request: web.Request) -> web.Response:
    u = await _auth_user(request)
    if not u["study_group_id"]:
        raise web.HTTPForbidden(text="need group")
    try:
        msg_id = int(request.match_info["msg_id"])
    except (KeyError, ValueError, TypeError):
        raise web.HTTPBadRequest(text="bad message id")
    async with async_session_maker() as session:
        stmt = (
            select(GroupEmailMessage, GroupEmailMailbox.title)
            .outerjoin(
                GroupEmailMailbox,
                GroupEmailMessage.mailbox_id == GroupEmailMailbox.id,
            )
            .where(
                GroupEmailMessage.id == msg_id,
                GroupEmailMessage.study_group_id == u["study_group_id"],
            )
        )
        row = (await session.execute(stmt)).one_or_none()
        if not row:
            raise web.HTTPNotFound()
        msg, mb_title = row
        att_rows = list(
            (
                await session.scalars(
                    select(GroupEmailAttachment)
                    .where(GroupEmailAttachment.message_id == msg.id)
                    .order_by(GroupEmailAttachment.id)
                )
            ).all()
        )
        att_json = [
            {
                "id": a.id,
                "filename": a.filename or "",
                "size_bytes": a.size_bytes,
                "mime": a.mime_type or "application/octet-stream",
            }
            for a in att_rows
        ]
    return web.json_response(
        {
            "id": msg.id,
            "sender": msg.sender or "",
            "subject": msg.subject or "",
            "body_text": msg.body_preview or "",
            "received_at": (
                msg.received_at.isoformat() if msg.received_at else None
            ),
            "mailbox_title": mb_title,
            "attachments": att_json,
        }
    )


async def api_group_mail_attachment(request: web.Request) -> web.Response:
    u = await _auth_user(request)
    if not u["study_group_id"]:
        raise web.HTTPForbidden(text="need group")
    try:
        msg_id = int(request.match_info["msg_id"])
        att_id = int(request.match_info["att_id"])
    except (KeyError, ValueError, TypeError):
        raise web.HTTPBadRequest(text="bad id")
    async with async_session_maker() as session:
        att = await session.get(GroupEmailAttachment, att_id)
        if not att or att.message_id != msg_id:
            raise web.HTTPNotFound()
        msg = await session.get(GroupEmailMessage, msg_id)
        if not msg or msg.study_group_id != u["study_group_id"]:
            raise web.HTTPNotFound()
        stored = att.stored_path or ""
        dl_name = ascii_download_filename(att.filename)
        mime = (att.mime_type or "application/octet-stream").strip() or "application/octet-stream"
    if not is_safe_attachment_stored_path(stored):
        raise web.HTTPForbidden()
    path = (config.BASE_DIR / stored).resolve()
    if not path.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(
        path,
        headers={
            "Content-Type": mime,
            "Content-Disposition": f'attachment; filename="{dl_name}"',
        },
    )


async def api_group_members(request: web.Request) -> web.Response:
    u = await _auth_user(request)
    if not u["study_group_id"]:
        return web.json_response({"need_group": True, "group_name": None, "items": []})
    async with async_session_maker() as session:
        sg, members = await get_group_member_rows(session, u["study_group_id"])
        if not sg:
            return web.json_response({"need_group": True, "group_name": None, "items": []})
        items = [
            {
                "id": m.id,
                "full_name": m.full_name,
                "role": m.role.value,
                "telegram_id": m.telegram_id,
            }
            for m in members
        ]
    return web.json_response(
        {"need_group": False, "group_name": sg.name, "items": items}
    )


async def api_group_kick(request: web.Request) -> web.Response:
    u = await _auth_user(request)
    if not _is_chief_elder_web(u):
        raise web.HTTPForbidden(text="only chief elder")
    if not u["study_group_id"]:
        raise web.HTTPBadRequest(text="no study group")
    try:
        body = await request.json()
    except (json.JSONDecodeError, TypeError, ValueError):
        raise web.HTTPBadRequest(text="invalid json")
    try:
        target_id = int(body.get("user_id", 0))
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(text="bad user_id")
    if target_id <= 0:
        raise web.HTTPBadRequest(text="user_id required")
    async with async_session_maker() as session:
        actor = await session.get(User, u["id"])
        if not actor:
            raise web.HTTPForbidden(text="user not found")
        ok, err = await elder_remove_student(session, actor, target_id)
        if not ok:
            raise web.HTTPBadRequest(text=err)
        await session.commit()
    return web.json_response({"ok": True})


async def api_group_deputy(request: web.Request) -> web.Response:
    u = await _auth_user(request)
    if not _is_chief_elder_web(u):
        raise web.HTTPForbidden(text="only chief elder")
    if not u["study_group_id"]:
        raise web.HTTPBadRequest(text="no study group")
    try:
        body = await request.json()
    except (json.JSONDecodeError, TypeError, ValueError):
        raise web.HTTPBadRequest(text="invalid json")
    raw = body.get("user_id")
    target_id: int | None
    if raw is None or raw == "":
        target_id = None
    else:
        try:
            target_id = int(raw)
        except (TypeError, ValueError):
            raise web.HTTPBadRequest(text="bad user_id")
        if target_id <= 0:
            raise web.HTTPBadRequest(text="bad user_id")
    async with async_session_maker() as session:
        chief = await session.get(User, u["id"])
        if not chief:
            raise web.HTTPForbidden(text="user not found")
        ok, err = await set_group_deputy(session, chief, target_id)
        if not ok:
            raise web.HTTPBadRequest(text=err)
        await session.commit()
    return web.json_response({"ok": True})


async def api_schedule_week(request: web.Request) -> web.Response:
    u = await _auth_user(request)
    if not u["study_group_id"]:
        return web.json_response({"items": [], "need_group": True})
    today = date.today()
    end = today + timedelta(days=7)
    async with async_session_maker() as session:
        sg = await session.get(StudyGroup, u["study_group_id"])
        if sg and await schedule_cache_needs_expansion(
            session, u["study_group_id"], today, end
        ):
            if await ensure_schedule_cache_covers_range(session, sg, today, end):
                await session.commit()
        stmt = (
            select(Schedule)
            .where(
                Schedule.study_group_id == u["study_group_id"],
                Schedule.lesson_date >= today,
                Schedule.lesson_date <= end,
            )
            .order_by(Schedule.lesson_date, Schedule.start_time)
        )
        sched = list(await session.scalars(stmt))
        if not sg:
            sg = await session.get(StudyGroup, u["study_group_id"])
        if await _resync_schedule_cache_if_stale(session, sg, sched):
            sched = list(await session.scalars(stmt))
        rows = [
            {
                "id": s.id,
                "date": s.lesson_date.isoformat() if s.lesson_date else None,
                "start": s.start_time,
                "end": s.end_time,
                "subject": s.subject,
                "kind": (s.lesson_kind or "").strip() or None,
                "teacher": s.teacher,
                "room": s.room,
                "contingent": (s.contingent_label or "").strip() or None,
            }
            for s in sched
        ]
    return web.json_response({"items": rows})


async def api_schedule_today(request: web.Request) -> web.Response:
    u = await _auth_user(request)
    if not u["study_group_id"]:
        return web.json_response({"items": [], "need_group": True})
    today = date.today()
    async with async_session_maker() as session:
        sg = await session.get(StudyGroup, u["study_group_id"])
        if sg and await schedule_cache_needs_expansion(
            session, u["study_group_id"], today, today
        ):
            if await ensure_schedule_cache_covers_range(session, sg, today, today):
                await session.commit()
        stmt = (
            select(Schedule)
            .where(
                Schedule.study_group_id == u["study_group_id"],
                Schedule.lesson_date == today,
            )
            .order_by(Schedule.start_time)
        )
        sched = list(await session.scalars(stmt))
        if not sg:
            sg = await session.get(StudyGroup, u["study_group_id"])
        if await _resync_schedule_cache_if_stale(session, sg, sched):
            sched = list(await session.scalars(stmt))
        rows = [
            {
                "id": s.id,
                "start": s.start_time,
                "end": s.end_time,
                "subject": s.subject,
                "kind": (s.lesson_kind or "").strip() or None,
                "teacher": s.teacher,
                "room": s.room,
                "contingent": (s.contingent_label or "").strip() or None,
            }
            for s in sched
        ]
    return web.json_response({"items": rows})


async def api_schedule_week_window(request: web.Request) -> web.Response:
    """Понедельник–воскресенье; опционально ?week_start=YYYY-MM-DD (любой день недели → нормализуется до пн)."""
    u = await _auth_user(request)
    if not u["study_group_id"]:
        return web.json_response(
            {
                "items": [],
                "need_group": True,
                "week_start": None,
                "week_end": None,
            }
        )
    parsed = _parse_iso_date_query(request.rel_url.query.get("week_start"))
    if parsed is None:
        week_start = _monday_week_start(date.today())
    else:
        week_start = _monday_week_start(parsed)
    week_end = week_start + timedelta(days=6)
    async with async_session_maker() as session:
        week_stmt = (
            select(Schedule)
            .where(
                Schedule.study_group_id == u["study_group_id"],
                Schedule.lesson_date >= week_start,
                Schedule.lesson_date <= week_end,
            )
            .order_by(Schedule.lesson_date, Schedule.start_time)
        )
        schedules = list(await session.scalars(week_stmt))
        sg = await session.get(StudyGroup, u["study_group_id"])
        if sg and await schedule_cache_needs_expansion(
            session, u["study_group_id"], week_start, week_end
        ):
            if await ensure_schedule_cache_covers_range(session, sg, week_start, week_end):
                await session.commit()
                schedules = list(await session.scalars(week_stmt))
        if await _resync_schedule_cache_if_stale(session, sg, schedules):
            schedules = list(await session.scalars(week_stmt))
        ids = [s.id for s in schedules]
        hw_ids: set[int] = set()
        if ids:
            hq = await session.scalars(
                select(Homework.schedule_id)
                .where(Homework.schedule_id.in_(ids))
                .distinct()
            )
            hw_ids = {i for i in hq if i is not None}
        rows = [
            {
                "id": s.id,
                "date": s.lesson_date.isoformat() if s.lesson_date else None,
                "start": s.start_time,
                "end": s.end_time,
                "subject": s.subject,
                "kind": (s.lesson_kind or "").strip() or None,
                "teacher": s.teacher,
                "room": s.room,
                "contingent": (s.contingent_label or "").strip() or None,
                "has_homework": s.id in hw_ids,
            }
            for s in schedules
        ]
    return web.json_response(
        {
            "week_start": week_start.isoformat(),
            "week_end": week_end.isoformat(),
            "items": rows,
        }
    )


async def api_lesson_homework(request: web.Request) -> web.Response:
    u = await _auth_user(request)
    if not u["study_group_id"]:
        raise web.HTTPForbidden(text="need group")
    try:
        schedule_id = int(request.match_info["schedule_id"])
    except (KeyError, ValueError, TypeError):
        raise web.HTTPBadRequest(text="bad schedule id")
    async with async_session_maker() as session:
        sch = await session.get(Schedule, schedule_id)
        if not sch or sch.study_group_id != u["study_group_id"]:
            raise web.HTTPNotFound()
        hq = await session.scalars(
            select(Homework)
            .where(Homework.schedule_id == schedule_id)
            .order_by(Homework.created_at.desc())
        )
        hw_rows = list(hq)
    lesson = {
        "id": sch.id,
        "date": sch.lesson_date.isoformat() if sch.lesson_date else None,
        "start": sch.start_time,
        "end": sch.end_time,
        "subject": sch.subject,
        "kind": (sch.lesson_kind or "").strip() or None,
        "teacher": sch.teacher,
        "room": sch.room,
        "contingent": (sch.contingent_label or "").strip() or None,
    }
    entries = [
        {"id": h.id, "description": h.description or "", "files": h.file_list()}
        for h in hw_rows
    ]
    return web.json_response({"lesson": lesson, "entries": entries})


async def api_schedule_subjects(request: web.Request) -> web.Response:
    u = await _auth_user(request)
    if not u["study_group_id"]:
        return web.json_response({"items": [], "need_group": True})
    sem_raw = request.query.get("semester")
    if not sem_raw or not str(sem_raw).strip().isdigit():
        raise web.HTTPBadRequest(text="semester query required")
    semester = int(sem_raw)
    async with async_session_maker() as session:
        items = await subjects_for_group_semester(session, u["study_group_id"], semester)
    return web.json_response({"items": items, "semester": semester})


async def api_semesters(request: web.Request) -> web.Response:
    u = await _auth_user(request)
    if not u["study_group_id"]:
        return web.json_response(
            {"items": [], "need_group": True, "current_semester": None}
        )
    async with async_session_maker() as session:
        sg = await session.get(StudyGroup, u["study_group_id"])
        items = await semester_numbers_for_group(session, u["study_group_id"])
    return web.json_response(
        {
            "items": items,
            "current_semester": sg.semester_number if sg else None,
        }
    )


async def api_grades_get(request: web.Request) -> web.Response:
    u = await _auth_user(request)
    semester = request.query.get("semester")
    sem_int = (
        int(semester)
        if semester is not None and str(semester).strip().isdigit()
        else None
    )
    today = datetime.now(ZoneInfo(config.TZ)).date()
    async with async_session_maker() as session:
        stmt = select(Grade).where(Grade.user_id == u["id"])
        if sem_int is not None:
            stmt = stmt.where(Grade.semester_number == sem_int)
        q = await session.scalars(stmt.order_by(Grade.semester_number, Grade.subject))
        grade_rows = list(q)

        slots: dict[str, tuple[int, int]] = {}
        if u["study_group_id"]:
            slots = await attendance_slots_by_subject_key(
                session, u["study_group_id"], u["id"], today
            )

        rows: list[dict] = []
        for g in grade_rows:
            pres, tot = slots.get(g.subject_key, (0, 0))
            rows.append(
                {
                    "subject": g.subject,
                    "points": g.points,
                    "semester_number": g.semester_number,
                    "attendance_present": pres,
                    "attendance_total": tot,
                    "attendance_points": (
                        round(100 * pres / tot) if tot > 0 else None
                    ),
                }
            )

        attendance_overall: dict[str, int | None] | None = None
        if u["study_group_id"]:
            if sem_int is not None:
                cat_keys = await semester_subject_keys(
                    session, u["study_group_id"], sem_int
                )
                attendance_overall = aggregate_attendance(slots, cat_keys)
            else:
                keys = list(dict.fromkeys(g.subject_key for g in grade_rows))
                if keys:
                    attendance_overall = aggregate_attendance(slots, keys)

    return web.json_response({"items": rows, "attendance_overall": attendance_overall})


async def api_grades_post(request: web.Request) -> web.Response:
    u = await _auth_user(request)
    if not _has_elder_privileges(u):
        raise web.HTTPForbidden()
    if not u["study_group_id"]:
        raise web.HTTPBadRequest(text="no study group")
    body = await request.json()
    target_tid = int(body.get("target_telegram_id", 0))
    subject = (body.get("subject") or "").strip()
    points = float(body.get("points", 0))
    sem_raw = body.get("semester_number")
    try:
        semester_number = int(sem_raw)
    except (TypeError, ValueError):
        semester_number = 0
    if not target_tid or not subject or not semester_number:
        raise web.HTTPBadRequest()
    sk = subject_key(subject)
    async with async_session_maker() as session:
        allowed = await subjects_for_group_semester(
            session, u["study_group_id"], semester_number
        )
        if subject not in allowed:
            raise web.HTTPBadRequest(text="subject not in semester catalog")
        target = await session.scalar(
            select(User).where(User.telegram_id == target_tid)
        )
        if not target:
            raise web.HTTPNotFound()
        if target.study_group_id != u["study_group_id"]:
            raise web.HTTPForbidden()
        ex = await session.scalar(
            select(Grade).where(
                Grade.user_id == target.id,
                Grade.semester_number == semester_number,
                Grade.subject_key == sk,
            )
        )
        if ex:
            ex.points = points
            ex.subject = subject
        else:
            session.add(
                Grade(
                    user_id=target.id,
                    semester_number=semester_number,
                    subject=subject,
                    subject_key=sk,
                    points=points,
                )
            )
        await session.commit()
    return web.json_response({"ok": True})


async def api_site_grades_get(request: web.Request) -> web.Response:
    u = await _auth_user(request)
    if not u["study_group_id"]:
        return web.json_response({"items": [], "need_group": True})
    async with async_session_maker() as session:
        usr = await session.get(User, u["id"])
        if not usr:
            return web.json_response({"items": []})
        q = await session.scalars(
            select(SiteGrade)
            .where(
                SiteGrade.study_group_id == u["study_group_id"],
                or_(
                    SiteGrade.telegram_id == usr.telegram_id,
                    SiteGrade.student_name == usr.full_name,
                ),
            )
            .order_by(SiteGrade.discipline)
        )
        rows = [
            {
                "discipline": g.discipline,
                "total_score": g.total_score,
                "attendance_percent": g.attendance_percent,
                "marks_display": _site_marks_display(g.marks_json),
            }
            for g in q
        ]
    return web.json_response({"items": rows})


async def api_homework_upload(request: web.Request) -> web.Response:
    u = await _auth_user(request)
    if not _has_elder_privileges(u):
        raise web.HTTPForbidden()
    if not u["study_group_id"]:
        raise web.HTTPBadRequest(text="no study group")
    ct = (request.headers.get("Content-Type") or "").lower()
    if "multipart/form-data" in ct:
        reader = await request.multipart()
        field = await reader.next()
        if field is None:
            raise web.HTTPBadRequest(text="empty body")
        if field.name != "file":
            raise web.HTTPBadRequest(text='expected field "file"')
        data = await field.read(decode=False)
        filename = field.filename or "file.bin"
    else:
        # application/octet-stream: через PHP-прокси тело попадает в php://input;
        # multipart на многих хостингах в php://input пустой → зависание бэкенда.
        data = await request.read()
        filename = (request.query.get("filename") or "").strip() or "file.bin"
    if not data:
        raise web.HTTPBadRequest(text="empty file")
    if len(data) > config.HOMEWORK_UPLOAD_MAX_BYTES:
        raise web.HTTPRequestEntityTooLarge(
            text=f"Файл больше {config.HOMEWORK_UPLOAD_MAX_MB} МБ"
        )
    safe = "".join(c for c in Path(filename).name if c.isalnum() or c in "._-")[:100] or "file"
    upload_root = _homework_web_upload_dir()
    upload_root.mkdir(parents=True, exist_ok=True)
    dest = upload_root / f"{u['telegram_id']}_{uuid.uuid4().hex[:12]}_{safe}"
    try:
        dest.write_bytes(data)
    except OSError:
        logger.exception("homework upload write_bytes failed")
        raise web.HTTPInternalServerError(
            text="Не удалось сохранить файл (место на диске или права на каталог загрузок)."
        ) from None
    rel = _relpath_under_base_for_api_file(dest)
    return web.json_response({"path": rel})


async def api_homework_post(request: web.Request) -> web.Response:
    u = await _auth_user(request)
    if not _has_elder_privileges(u):
        raise web.HTTPForbidden()
    if not u["study_group_id"]:
        raise web.HTTPBadRequest(text="no study group")
    body = await request.json()
    try:
        schedule_id = int(body.get("schedule_id", 0))
    except (TypeError, ValueError):
        schedule_id = 0
    if not schedule_id:
        raise web.HTTPBadRequest(text="schedule_id required")
    description = (body.get("description") or "").strip()
    raw_paths = body.get("file_paths") or []
    if not isinstance(raw_paths, list):
        raw_paths = []
    validated: list[str] = []
    for p in raw_paths:
        if isinstance(p, str):
            v = _validate_homework_file_rel(p)
            if v:
                validated.append(v)
    async with async_session_maker() as session:
        sch = await session.get(Schedule, schedule_id)
        if not sch or sch.study_group_id != u["study_group_id"]:
            raise web.HTTPNotFound()
        hw = Homework(
            schedule_id=schedule_id,
            description=description,
            file_paths=json.dumps(validated, ensure_ascii=False),
        )
        session.add(hw)
        await session.flush()
        session.add(deadline_for_homework(hw, sch, creator_user_id=u["id"]))
        await session.commit()
    return web.json_response({"ok": True})


async def api_homework_delete(request: web.Request) -> web.Response:
    u = await _auth_user(request)
    if not _has_elder_privileges(u):
        raise web.HTTPForbidden(text="only elder")
    if not u["study_group_id"]:
        raise web.HTTPBadRequest(text="no study group")
    try:
        homework_id = int(request.match_info["homework_id"])
    except (KeyError, ValueError, TypeError):
        raise web.HTTPBadRequest(text="bad homework id")
    async with async_session_maker() as session:
        ok, err = await delete_homework_for_study_group(
            session, homework_id, u["study_group_id"]
        )
        if not ok:
            raise web.HTTPBadRequest(text=err)
        await session.commit()
    return web.json_response({"ok": True})


async def api_materials(request: web.Request) -> web.Response:
    u = await _auth_user(request)
    if not u["study_group_id"]:
        return web.json_response({"items": [], "need_group": True})
    async with async_session_maker() as session:
        stmt = (
            select(Homework, Schedule)
            .join(Schedule, Homework.schedule_id == Schedule.id)
            .where(Schedule.study_group_id == u["study_group_id"])
            .order_by(Homework.created_at.desc())
        )
        res = await session.execute(stmt)
        items = []
        for hw, sch in res:
            items.append(
                {
                    "id": hw.id,
                    "description": hw.description,
                    "files": hw.file_list(),
                    "schedule": {
                        "date": sch.lesson_date.isoformat() if sch.lesson_date else None,
                        "start": sch.start_time,
                        "subject": sch.subject,
                    },
                }
            )
    return web.json_response({"items": items})


async def api_santa(request: web.Request) -> web.Response:
    u = await _auth_user(request)
    if not u["study_group_id"]:
        return web.json_response({"active": False, "receiver_name": None, "need_group": True})
    year = date.today().year
    async with async_session_maker() as session:
        game = await session.scalar(
            select(SantaGame).where(
                SantaGame.study_group_id == u["study_group_id"],
                SantaGame.year == year,
                SantaGame.is_active.is_(True),
            )
        )
        if not game:
            return web.json_response({"active": False, "receiver_name": None})
        pair = await session.scalar(
            select(SantaPair).where(
                SantaPair.game_id == game.id,
                SantaPair.giver_id == u["id"],
            )
        )
        if not pair:
            return web.json_response({"active": True, "receiver_name": None})
        recv = await session.get(User, pair.receiver_id)
        name = recv.full_name if recv else "?"
        return web.json_response({"active": True, "receiver_name": name})


async def api_deadlines(request: web.Request) -> web.Response:
    u = await _auth_user(request)
    if not u["study_group_id"]:
        return web.json_response({"items": [], "need_group": True})
    from datetime import datetime

    now = datetime.now()
    async with async_session_maker() as session:
        q = await session.scalars(
            select(Deadline)
            .where(
                Deadline.study_group_id == u["study_group_id"],
                Deadline.deadline_date >= now,
            )
            .order_by(Deadline.deadline_date)
        )
        deadlines = list(q)
        rows = []
        for d in deadlines:
            item = {
                "id": d.id,
                "title": d.title,
                "description": d.description or "",
                "subject": d.subject or "",
                "deadline": d.deadline_date.isoformat(),
                "files": [],
            }
            if d.homework_id:
                item["homework_id"] = d.homework_id
                hw = await session.get(Homework, d.homework_id)
                if hw:
                    item["files"] = hw.file_list()
            rows.append(item)
    return web.json_response({"items": rows})


async def api_deadlines_post(request: web.Request) -> web.Response:
    u = await _auth_user(request)
    if not _has_elder_privileges(u):
        raise web.HTTPForbidden(text="only elder")
    if not u["study_group_id"]:
        raise web.HTTPBadRequest(text="no study group")
    try:
        body = await request.json()
    except (json.JSONDecodeError, TypeError, ValueError):
        raise web.HTTPBadRequest(text="invalid json")
    title = (body.get("title") or "").strip()
    if not title:
        raise web.HTTPBadRequest(text="title required")
    desc = (body.get("description") or "").strip()
    subj_raw = (body.get("subject") or "").strip()
    subject = subj_raw if subj_raw else None
    deadline_raw = body.get("deadline") or body.get("deadline_at") or ""
    dt_naive = _parse_deadline_datetime_naive(str(deadline_raw))
    if not dt_naive:
        raise web.HTTPBadRequest(
            text="invalid deadline (use YYYY-MM-DDTHH:MM or DD.MM.YYYY HH:MM)"
        )
    now_naive = datetime.now(ZoneInfo(config.TZ)).replace(tzinfo=None)
    if dt_naive <= now_naive:
        raise web.HTTPBadRequest(text="deadline must be in the future")
    async with async_session_maker() as session:
        session.add(
            Deadline(
                study_group_id=u["study_group_id"],
                title=title[:512],
                description=desc[:20000],
                deadline_date=dt_naive,
                subject=subject[:512] if subject else None,
                created_by=u["id"],
                notified_24h=False,
            )
        )
        await session.commit()
    return web.json_response({"ok": True})


async def api_attendance_get(request: web.Request) -> web.Response:
    u = await _auth_user(request)
    if not u["study_group_id"]:
        return web.json_response({"items": [], "need_group": True})
    today = date.today()
    async with async_session_maker() as session:
        q = await session.scalars(
            select(Schedule)
            .where(
                Schedule.study_group_id == u["study_group_id"],
                Schedule.lesson_date == today,
            )
            .order_by(Schedule.start_time)
        )
        lessons = list(q)
        marked_ids: set[int] = set()
        if lessons:
            att_q = await session.scalars(
                select(Attendance).where(
                    Attendance.user_id == u["id"],
                    Attendance.mark_date == today,
                )
            )
            marked_ids = {a.schedule_id for a in att_q}
        rows = [
            {
                "id": s.id,
                "start": s.start_time,
                "end": s.end_time,
                "subject": s.subject,
                "kind": (s.lesson_kind or "").strip() or None,
                "contingent": (s.contingent_label or "").strip() or None,
                "marked": s.id in marked_ids,
            }
            for s in lessons
        ]
    return web.json_response({"items": rows, "date": today.isoformat()})


async def api_attendance_post(request: web.Request) -> web.Response:
    u = await _auth_user(request)
    if not u["study_group_id"]:
        raise web.HTTPBadRequest(text="no study group")
    body = await request.json()
    schedule_id = int(body.get("schedule_id", 0))
    if not schedule_id:
        raise web.HTTPBadRequest(text="schedule_id required")
    today = date.today()
    async with async_session_maker() as session:
        sch = await session.get(Schedule, schedule_id)
        if not sch or sch.study_group_id != u["study_group_id"]:
            raise web.HTTPNotFound()
        existing = await session.scalar(
            select(Attendance).where(
                Attendance.user_id == u["id"],
                Attendance.schedule_id == schedule_id,
                Attendance.mark_date == today,
            )
        )
        if not existing:
            session.add(
                Attendance(
                    user_id=u["id"],
                    schedule_id=schedule_id,
                    mark_date=today,
                    is_present=True,
                )
            )
            await session.commit()
    return web.json_response({"ok": True})


async def api_attendance_group_report(request: web.Request) -> web.Response:
    u = await _auth_user(request)
    if not _has_elder_privileges(u):
        raise web.HTTPForbidden(text="only elder")
    if not u["study_group_id"]:
        return web.json_response({"need_group": True})
    raw = (request.query.get("date") or "").strip()
    if not raw:
        raise web.HTTPBadRequest(text="date required (YYYY-MM-DD)")
    try:
        report_date = date.fromisoformat(raw[:10])
    except ValueError:
        raise web.HTTPBadRequest(text="invalid date")
    if not (date(2015, 1, 1) <= report_date <= date.today() + timedelta(days=366)):
        raise web.HTTPBadRequest(text="date out of range")
    async with async_session_maker() as session:
        sg = await session.get(StudyGroup, u["study_group_id"])
        if not sg:
            raise web.HTTPNotFound()
        data = await attendance_group_day_stats(session, sg, report_date)
    return web.json_response(data)


async def api_birthdays(request: web.Request) -> web.Response:
    u = await _auth_user(request)
    if not u["study_group_id"]:
        return web.json_response({"items": [], "need_group": True})
    from utils.birthday_helpers import days_until_birthday, format_birthday_display

    today = date.today()
    async with async_session_maker() as session:
        q = await session.scalars(
            select(User).where(
                User.study_group_id == u["study_group_id"],
                User.birthday_month.isnot(None),
                User.birthday_day.isnot(None),
            )
        )
        people = list(q)

    def sort_key(p: User) -> int:
        return days_until_birthday(p.birthday_month, p.birthday_day, today)

    people.sort(key=sort_key)
    rows = [
        {
            "name": p.full_name,
            "display": format_birthday_display(p.birthday_day, p.birthday_month, p.birth_year),
            "days_left": days_until_birthday(p.birthday_month, p.birthday_day, today),
        }
        for p in people
    ]
    return web.json_response({"items": rows})


async def api_uploaded_file(request: web.Request) -> web.StreamResponse:
    await _auth_user(request)
    rel = (request.query.get("path") or "").strip().replace("\\", "/")
    if not rel or ".." in rel:
        raise web.HTTPForbidden()
    path = (config.BASE_DIR / rel).resolve()
    try:
        path.relative_to(config.BASE_DIR.resolve())
    except ValueError:
        raise web.HTTPForbidden()
    if not path.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(path)


async def root_redirect(request: web.Request) -> web.Response:
    raise web.HTTPFound(location="/index.html")


@web.middleware
async def _static_cache_middleware(request: web.Request, handler):
    resp = await handler(request)
    if request.method != "GET" or request.path.startswith("/api/"):
        return resp
    path = request.path.lower()
    if path.endswith(".js") or path.endswith(".css"):
        resp.headers.setdefault("Cache-Control", "public, max-age=86400")
    elif path.endswith(".html") or path in ("/", ""):
        resp.headers.setdefault("Cache-Control", "private, max-age=120")
    return resp


async def api_students(request: web.Request) -> web.Response:
    u = await _auth_user(request)
    if not _has_elder_privileges(u):
        raise web.HTTPForbidden()
    if not u["study_group_id"]:
        raise web.HTTPBadRequest(text="no study group")
    async with async_session_maker() as session:
        q = await session.scalars(
            select(User).where(
                User.study_group_id == u["study_group_id"],
                User.role == UserRole.student,
            )
        )
        rows = [
            {"telegram_id": s.telegram_id, "full_name": s.full_name} for s in q
        ]
    return web.json_response({"items": rows})


def create_app() -> web.Application:
    app = web.Application(
        client_max_size=config.HOMEWORK_UPLOAD_MAX_BYTES + 1024 * 1024
    )
    app.middlewares.append(_static_cache_middleware)
    app.router.add_get("/", root_redirect)
    app.router.add_get("/api/me", api_me)
    app.router.add_get("/api/schedule/today", api_schedule_today)
    app.router.add_get("/api/schedule/week", api_schedule_week)
    app.router.add_get("/api/schedule/week_window", api_schedule_week_window)
    app.router.add_get(
        r"/api/lesson/{schedule_id:\d+}/homework", api_lesson_homework
    )
    app.router.add_get("/api/schedule/subjects", api_schedule_subjects)
    app.router.add_get("/api/semesters", api_semesters)
    app.router.add_get("/api/grades", api_grades_get)
    app.router.add_post("/api/grades", api_grades_post)
    app.router.add_get("/api/site_grades", api_site_grades_get)
    app.router.add_post("/api/homework/upload", api_homework_upload)
    app.router.add_post("/api/homework", api_homework_post)
    app.router.add_delete(
        r"/api/homework/{homework_id:\d+}", api_homework_delete
    )
    app.router.add_get("/api/materials", api_materials)
    app.router.add_get("/api/santa", api_santa)
    app.router.add_get("/api/students", api_students)
    app.router.add_get("/api/group/mail", api_group_mail)
    app.router.add_get(r"/api/group/mail/{msg_id:\d+}", api_group_mail_one)
    app.router.add_get(
        r"/api/group/mail/{msg_id:\d+}/attachment/{att_id:\d+}",
        api_group_mail_attachment,
    )
    app.router.add_get("/api/group/members", api_group_members)
    app.router.add_post("/api/group/kick", api_group_kick)
    app.router.add_post("/api/group/deputy", api_group_deputy)
    app.router.add_get("/api/deadlines", api_deadlines)
    app.router.add_post("/api/deadlines", api_deadlines_post)
    app.router.add_get("/api/attendance", api_attendance_get)
    app.router.add_post("/api/attendance", api_attendance_post)
    app.router.add_get("/api/attendance/group_report", api_attendance_group_report)
    app.router.add_get("/api/birthdays", api_birthdays)
    app.router.add_get("/api/file", api_uploaded_file)
    app.router.add_static("/", path=str(_webapp_dir()), name="static", show_index=False)
    return app
