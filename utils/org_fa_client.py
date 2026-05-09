"""HTTP-клиент для API org.fa.ru (Bitrix): авторизация через cookies, список группы, баллы."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from datetime import date

import aiohttp
from yarl import URL

logger = logging.getLogger(__name__)

API_BASE = "https://org.fa.ru/bitrix/vuz/api"
COMMON_HEADERS = {
    "App-Key": "browser-bitrix",
    "App-Version": "8.96.3",
    "App-Locale": "ru",
    "App-Timezoneoffset": "-180",
}


@dataclass
class OrgStudent:
    id: int
    fullname: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class OrgMark:
    student_id: int
    mark_val: float
    mark_type: str


@dataclass
class OrgDisciplineGrade:
    discipline_name: str
    discipline_id: int | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class OrgStudentGrades:
    student_id: int
    contingent_title: str
    attendance_percent: int | None = None
    skip_all: int | None = None
    disciplines: list[OrgDisciplineGrade] = field(default_factory=list)


class OrgFaAuthError(Exception):
    pass


def parse_cookie_string(raw: str) -> dict[str, str]:
    """Принимает строку cookies из браузера (формат 'k=v; k2=v2') и возвращает dict."""
    result: dict[str, str] = {}
    for pair in raw.split(";"):
        pair = pair.strip()
        if "=" not in pair:
            continue
        key, _, val = pair.partition("=")
        result[key.strip()] = val.strip()
    return result


class OrgFaClient:
    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None
        self._profile_id: int | None = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers=COMMON_HEADERS)
        return self._session

    async def auth_with_cookies(self, cookie_string: str) -> None:
        """Установить cookies из строки браузера и проверить через bootstrap."""
        session = await self._ensure_session()
        cookies = parse_cookie_string(cookie_string)
        if not cookies:
            raise OrgFaAuthError("Не удалось распарсить cookies (пустая строка?).")

        for name, value in cookies.items():
            session.cookie_jar.update_cookies(
                {name: value}, URL("https://org.fa.ru/")
            )

        async with session.post(f"{API_BASE}/bootstrap", json={}) as resp:
            if resp.status != 200:
                raise OrgFaAuthError(
                    f"bootstrap вернул {resp.status} — cookies невалидны или устарели."
                )
            data = await resp.json()
            profile = data.get("profile")
            if not profile or not profile.get("id"):
                raise OrgFaAuthError(
                    "Cookies не дали авторизацию: bootstrap не вернул профиль."
                )
            self._profile_id = profile["id"]
            logger.info("org.fa.ru cookie auth OK, profile_id=%s", self._profile_id)

    async def get_my_group(self) -> list[OrgStudent]:
        session = await self._ensure_session()
        for path in ("interaction/myGroup", "myGroup"):
            try:
                async with session.post(f"{API_BASE}/{path}", json={}) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json()
                    raw = data.get("data") or data.get("students") or []
                    if isinstance(raw, list) and raw:
                        return [
                            OrgStudent(
                                id=s["id"],
                                fullname=s.get("fullname", ""),
                                raw=dict(s) if isinstance(s, dict) else {},
                            )
                            for s in raw
                            if "id" in s
                        ]
            except Exception:
                continue
        return []

    async def get_student_grades(
        self,
        student_id: int,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> OrgStudentGrades | None:
        session = await self._ensure_session()
        today = date.today()
        if not date_from:
            date_from = f"{today.year}-01-01"
        if not date_to:
            date_to = f"{today.year}-12-31"

        payload = {
            "student_id": student_id,
            "date_from": date_from,
            "date_to": date_to,
        }
        async with session.post(
            f"{API_BASE}/atlog/get_journals_by_contingent",
            json=payload,
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()

        if not data:
            return None

        disciplines: list[OrgDisciplineGrade] = []
        for d in data.get("disciplines") or []:
            disciplines.append(
                OrgDisciplineGrade(
                    discipline_name=d.get("discipline_name", ""),
                    discipline_id=d.get("discipline_id"),
                    raw=d,
                )
            )

        return OrgStudentGrades(
            student_id=student_id,
            contingent_title=data.get("contingent_title", ""),
            attendance_percent=data.get("attendance_percent"),
            skip_all=data.get("skip_all"),
            disciplines=disciplines,
        )

    async def get_journal_marks(self, discipline_id: int) -> dict[int, list[OrgMark]]:
        """Получить баллы (all_marks) по всем занятиям дисциплины.

        Возвращает {student_id: [OrgMark, ...]} — все оценки из журнала.
        """
        session = await self._ensure_session()
        async with session.post(
            f"{API_BASE}/atlog/get_journal",
            json={"discipline_id": discipline_id},
        ) as resp:
            if resp.status != 200:
                return {}
            data = await resp.json()

        result: dict[int, list[OrgMark]] = {}
        for lesson in data.get("lessons") or []:
            for m in lesson.get("all_marks") or []:
                sid = m.get("student_id")
                val = m.get("mark_val")
                mtype = m.get("mark_type", "")
                if sid is None or val is None:
                    continue
                try:
                    val = float(val)
                except (ValueError, TypeError):
                    continue
                result.setdefault(sid, []).append(
                    OrgMark(student_id=sid, mark_val=val, mark_type=mtype)
                )
        return result

    async def get_journal_students(self, discipline_id: int) -> list[OrgStudent]:
        """Получить список студентов из журнала дисциплины."""
        session = await self._ensure_session()
        async with session.post(
            f"{API_BASE}/atlog/get_journal",
            json={"discipline_id": discipline_id},
        ) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
        students = []
        for s in data.get("data") or []:
            if "id" in s and isinstance(s, dict):
                students.append(
                    OrgStudent(
                        id=s["id"],
                        fullname=s.get("fullname", ""),
                        raw=dict(s),
                    )
                )
        return students

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
