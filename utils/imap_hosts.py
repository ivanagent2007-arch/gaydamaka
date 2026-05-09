"""Подбор IMAP-сервера по адресу ящика (если в .env не задан IMAP_HOST)."""

from __future__ import annotations

import os


def resolve_imap_host_for_email(login_email: str, env_imap_host: str | None = None) -> str:
    """
    env_imap_host — значение IMAP_HOST из конфигурации; если непустое, используется оно.
    Иначе хост определяется по домену (популярные почтовые сервисы и типичный корп. imap.домен).
    """
    override = (env_imap_host if env_imap_host is not None else os.getenv("IMAP_HOST", "")).strip()
    if override:
        return override

    if "@" not in (login_email or ""):
        return "imap.mail.ru"

    dom = login_email.split("@", 1)[-1].lower().strip()

    # Яндекс
    if dom in ("yandex.ru", "ya.ru", "narod.ru") or dom.endswith(".yandex.ru"):
        return "imap.yandex.ru"

    # Google
    if dom in ("gmail.com", "googlemail.com") or dom.endswith(".googlemail.com"):
        return "imap.gmail.com"

    # Почта Mail.ru и близкие
    if dom in ("mail.ru", "bk.ru", "inbox.ru", "list.ru", "internet.ru"):
        return "imap.mail.ru"

    # Microsoft / Outlook / Office 365 (личная и школьная/рабочая на onmicrosoft.com)
    _ms_personal = frozenset(
        {
            "outlook.com",
            "outlook.jp",
            "outlook.de",
            "outlook.fr",
            "outlook.co.uk",
            "hotmail.com",
            "hotmail.co.uk",
            "hotmail.fr",
            "live.com",
            "live.ru",
            "msn.com",
        }
    )
    if dom in _ms_personal or dom.endswith(".onmicrosoft.com"):
        return "outlook.office365.com"

    # Yahoo
    if dom.startswith("yahoo.") or dom in ("ymail.com", "rocketmail.com"):
        return "imap.mail.yahoo.com"

    # iCloud
    if dom in ("icloud.com", "me.com", "mac.com"):
        return "imap.mail.me.com"

    # GMX / Web.de
    if dom in (
        "gmx.de",
        "gmx.net",
        "gmx.com",
        "gmx.at",
        "gmx.ch",
        "gmx.co.uk",
    ) or dom.endswith(".gmx.net"):
        return "imap.gmx.net"
    if dom == "web.de":
        return "imap.web.de"

    # Rambler
    if dom in ("rambler.ru", "lenta.ru", "autorambler.ru") or dom.endswith(".rambler.ru"):
        return "imap.rambler.ru"

    # Распространённые .ua / Украина
    if dom in ("ukr.net", "i.ua", "email.ua"):
        return "imap.ukr.net"

    # Zoho
    if dom.endswith(".zoho.com") or dom.endswith(".zoho.eu"):
        return "imap.zoho.com"

    # AOL
    if dom in ("aol.com", "games.com"):
        return "imap.aol.com"

    # Частый корпоративный шаблон; при ошибке входа задайте IMAP_HOST в .env
    return f"imap.{dom}"
