"""
Парсер расписания РУЗ Финуниверситета.
Основной источник: JSON API ruz.fa.ru (см. https://github.com/GeorgiyDemo/fa_api).
Дополнительно: разбор HTML главной страницы через BeautifulSoup (резерв).
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

import config

logger = logging.getLogger(__name__)


def _sync_get(url: str, timeout: float = 45.0) -> requests.Response:
    return requests.get(url, timeout=timeout)


def search_group_id(group_query: str, base_url: str | None = None) -> str | None:
    ruz = (base_url or config.RUZ_BASE_URL).rstrip("/")
    url = f"{ruz}/api/search?term={quote(group_query)}&type=group"
    try:
        r = requests.get(url, timeout=45)
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("RUZ search failed: %s", e)
        return None
    if not data:
        return None
    first = data[0]
    return str(first.get("id") or first.get("groupOid") or first.get("value"))


def _norm_subject(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, dict):
        return (
            str(raw.get("title") or raw.get("name") or raw.get("discipline") or "")
        ).strip()
    return str(raw).strip()


def _norm_teacher(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, dict):
        return str(raw.get("name") or raw.get("fullName") or raw.get("lecturer") or "").strip()
    return str(raw).strip()


def _norm_lesson_kind(item: dict[str, Any]) -> str:
    """Тип занятия из РУЗ (kindOfWork и др.) → короткая подпись для отображения."""
    raw: Any = None
    for key in (
        "kindOfWork",
        "kindOfWorkName",
        "kindOfWorkString",
        "kind_of_work",
        "lessonType",
        "type",
    ):
        v = item.get(key)
        if v is not None and str(v).strip():
            raw = v
            break
    if isinstance(raw, dict):
        raw = (
            raw.get("name")
            or raw.get("title")
            or raw.get("abbr")
            or raw.get("description")
            or ""
        )
    if raw is None:
        return ""
    s = str(raw).strip().lower()
    if not s:
        return ""
    # Сначала узкие фразы РУЗ: «семинарские» содержит «семин» раньше, чем «практ» в «Практическое».
    if "семин" in s or "seminar" in s:
        return "семинар"
    if "лек" in s or "lecture" in s or "лекционн" in s:
        return "лекция"
    if "практ" in s or "pract" in s:
        return "практика"
    if "лаб" in s or "lab" in s:
        return "лаб."
    if "консуль" in s:
        return "конс."
    return s[:48]


def _norm_room(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, dict):
        return str(raw.get("name") or raw.get("auditorium") or "").strip()
    return str(raw).strip()


# Типичные коды групп РУЗ: БИ25-2, ПИ19-5(4), ИСБ-12 (буквы-цифры) и т.п.
_RUZ_GROUP_CODE_RE = re.compile(
    r"(?<![0-9A-Za-zА-Яа-яЁё])"
    r"([А-ЯЁA-Za-z]{2,12}(?:\d{1,3}-\d+|-\d{1,4})(?:\([^)]{0,16}\))?)"
    r"(?![0-9A-Za-zА-Яа-яЁё])"
)


def _compact_ruz_group_title(raw: str) -> str:
    """Короткая подпись группы для строки как в интерфейсе РУЗ."""
    s = (raw or "").strip()
    if not s:
        return ""
    found = _RUZ_GROUP_CODE_RE.findall(s)
    if found:
        if len(found) == 1:
            return found[0].strip()
        return "; ".join(dict.fromkeys(f.strip() for f in found))
    parts = s.rsplit(" - ", 1)
    if len(parts) == 2:
        tail = parts[1].strip()
        if len(tail) <= 36:
            return tail
    return s if len(s) <= 80 else s[:77] + "…"


def _ruz_contingent_label(item: dict[str, Any], study_group_display_name: str) -> str:
    """Подгруппа / поток / группы из РУЗ — в том числе listGroups и поле group (как на сайте)."""
    del study_group_display_name  # раньше скрывали совпадение с названием группы — больше не нужно
    extras: list[str] = []

    for key in ("subGroup", "stream"):
        v = item.get(key)
        if v is None:
            continue
        t = str(v).strip()
        if not t or t.lower() in {"null", "none", "0"}:
            continue
        if t not in extras:
            extras.append(t)

    lst = item.get("listSubGroups") or []
    if isinstance(lst, list):
        for x in lst:
            if not isinstance(x, dict):
                continue
            label = ""
            for k in ("subGroup", "name", "title"):
                vv = x.get(k)
                if vv is not None and str(vv).strip():
                    label = str(vv).strip()
                    break
            if not label:
                gg = x.get("group")
                if gg is not None and str(gg).strip():
                    label = str(gg).strip()
            if label and label not in extras:
                extras.append(label)

    ps = (item.get("parentschedule") or "").strip()

    # Несколько групп в занятии (лекции на поток) — как в РУЗ: «Поток: БИ25-1; БИ25-2»
    lg_raw = item.get("listGroups") or []
    lg_compacts: list[str] = []
    if isinstance(lg_raw, list):
        for x in lg_raw:
            if not isinstance(x, dict):
                continue
            g = (x.get("group") or "").strip()
            if not g:
                continue
            c = _compact_ruz_group_title(g)
            if c and c not in lg_compacts:
                lg_compacts.append(c)

    main_line = ""
    if len(lg_compacts) >= 2:
        main_line = "Поток: " + "; ".join(lg_compacts)
    elif len(lg_compacts) == 1:
        main_line = lg_compacts[0]
    else:
        gtop = (item.get("group") or "").strip()
        if gtop:
            main_line = _compact_ruz_group_title(gtop)
            if not main_line:
                main_line = gtop[:120] + ("…" if len(gtop) > 120 else "")

    out_parts: list[str] = []
    for p in extras:
        if p and p not in out_parts:
            out_parts.append(p)
    if main_line and main_line not in out_parts:
        out_parts.append(main_line)

    out = " · ".join(out_parts)
    if not out and ps:
        out = ps
    return out[:255] if out else ""


def _parse_ruz_date(s: str | None) -> date | None:
    if not s:
        return None
    s = str(s).strip()
    if re.match(r"^\d{2}\.\d{2}\.\d{4}", s):
        try:
            return datetime.strptime(s[:10], "%d.%m.%Y").date()
        except ValueError:
            return None
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        try:
            return date.fromisoformat(s[:10])
        except ValueError:
            return None
    if re.match(r"^\d{4}\.\d{2}\.\d{2}", s):
        try:
            return datetime.strptime(s[:10], "%Y.%m.%d").date()
        except ValueError:
            return None
    return None


def parse_schedule_item(
    item: dict[str, Any],
    group_name: str,
    lesson_number: int,
) -> dict[str, Any] | None:
    raw_date = (
        item.get("date")
        or item.get("day")
        or item.get("lessonDate")
        or item.get("dateOfLesson")
    )
    lesson_date = _parse_ruz_date(str(raw_date) if raw_date else None)
    if not lesson_date:
        return None

    start = str(item.get("beginLesson") or item.get("startTime") or item.get("begin") or "")
    end = str(item.get("endLesson") or item.get("endTime") or item.get("end") or "")
    if not start:
        start = "09:00"
    if not end:
        end = "10:30"

    subject = _norm_subject(
        item.get("discipline")
        or item.get("disciplineTitle")
        or item.get("subject")
        or item.get("title")
    )
    if not subject:
        subject = "Пара"

    teacher = _norm_teacher(
        item.get("lecturer") or item.get("teacher") or item.get("lecturerName")
    )
    room = _norm_room(item.get("auditorium") or item.get("room") or item.get("auditoriumName"))

    dow = lesson_date.weekday()
    lesson_kind = _norm_lesson_kind(item)
    contingent = _ruz_contingent_label(item, group_name)

    return {
        "group_name": group_name,
        "lesson_date": lesson_date,
        "day_of_week": dow,
        "lesson_number": lesson_number,
        "subject": subject,
        "teacher": teacher,
        "room": room,
        "start_time": start[:8],
        "end_time": end[:8],
        "lesson_kind": lesson_kind,
        "contingent_label": contingent,
    }


def fetch_schedule_json(
    group_id: str,
    date_begin: date,
    date_end: date,
    base_url: str | None = None,
) -> list[dict[str, Any]]:
    ruz = (base_url or config.RUZ_BASE_URL).rstrip("/")
    start_s = date_begin.strftime("%Y.%m.%d")
    finish_s = date_end.strftime("%Y.%m.%d")
    url = (
        f"{ruz}/api/schedule/group/{group_id}"
        f"?start={start_s}&finish={finish_s}&lng=1"
    )
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        return []
    return data


def build_schedule_rows(group_name: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_day: dict[date, list[dict[str, Any]]] = {}
    for item in items:
        ld = _parse_ruz_date(
            str(
                item.get("date")
                or item.get("day")
                or item.get("lessonDate")
                or ""
            )
        )
        if not ld:
            continue
        by_day.setdefault(ld, []).append(item)

    rows: list[dict[str, Any]] = []
    for ld in sorted(by_day.keys()):
        day_items = sorted(
            by_day[ld],
            key=lambda x: str(x.get("beginLesson") or x.get("startTime") or "00:00"),
        )
        for n, it in enumerate(day_items, start=1):
            row = parse_schedule_item(it, group_name, n)
            if row:
                rows.append(row)
    return rows


def fetch_schedule_for_group(
    group_name: str,
    group_search: str | None = None,
    days_ahead: int = 14,
    base_url: str | None = None,
) -> list[dict[str, Any]]:
    gid = search_group_id(group_search or group_name, base_url=base_url)
    if not gid:
        logger.error("Группа не найдена в РУЗ: %s (url=%s)", group_search or group_name, base_url or config.RUZ_BASE_URL)
        return []
    today = date.today()
    end = today + timedelta(days=days_ahead)
    items = fetch_schedule_json(gid, today, end, base_url=base_url)
    return build_schedule_rows(group_name, items)


async def fetch_schedule_async(
    group_name: str,
    group_search: str | None = None,
    days_ahead: int = 14,
    base_url: str | None = None,
) -> list[dict[str, Any]]:
    return await asyncio.to_thread(
        fetch_schedule_for_group, group_name, group_search, days_ahead, base_url
    )


def parse_main_page_tables(html: str, group_name: str) -> list[dict[str, Any]]:
    """Резерв: вытащить текст из таблиц на /ruz/main (структура может отличаться)."""
    soup = BeautifulSoup(html, "html.parser")
    rows_out: list[dict[str, Any]] = []
    today = date.today()
    for table in soup.find_all("table"):
        for tr in table.find_all("tr")[1:]:
            cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
            if len(cells) < 3:
                continue
            text_join = " ".join(cells)
            times = re.findall(r"\d{1,2}:\d{2}", text_join)
            start_t = times[0] if times else "09:00"
            end_t = times[1] if len(times) > 1 else "10:30"
            subject = cells[0] if cells else "Пара"
            rows_out.append(
                {
                    "group_name": group_name,
                    "lesson_date": today,
                    "day_of_week": today.weekday(),
                    "lesson_number": len(rows_out) + 1,
                    "subject": subject[:500],
                    "teacher": cells[1] if len(cells) > 1 else "",
                    "room": cells[-1] if len(cells) > 2 else "",
                    "start_time": start_t,
                    "end_time": end_t,
                    "lesson_kind": "",
                    "contingent_label": "",
                }
            )
    return rows_out


def fetch_schedule_html_fallback(group_name: str, base_url: str | None = None) -> list[dict[str, Any]]:
    ruz = (base_url or config.RUZ_BASE_URL).rstrip("/")
    try:
        r = requests.get(f"{ruz}/ruz/main", timeout=45)
        r.raise_for_status()
        return parse_main_page_tables(r.text, group_name)
    except requests.RequestException as e:
        logger.warning("HTML fallback failed: %s", e)
        return []
