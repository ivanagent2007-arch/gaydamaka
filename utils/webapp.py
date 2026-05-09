"""Проверка подписи Telegram Web App initData (https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app)."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import parse_qsl

import config


def validate_init_data(init_data: str) -> dict[str, Any]:
    if not init_data:
        raise ValueError("empty init data")
    parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    hash_received = parsed.pop("hash", None)
    if not hash_received:
        raise ValueError("no hash")

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(
        b"WebAppData", config.BOT_TOKEN.encode(), hashlib.sha256
    ).digest()
    calculated = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()
    if calculated != hash_received:
        raise ValueError("invalid hash")

    auth_raw = parsed.get("auth_date")
    if auth_raw:
        try:
            auth_ts = int(auth_raw)
        except ValueError:
            raise ValueError("bad auth_date") from None
        if time.time() - auth_ts > 86400:
            raise ValueError("auth expired, reopen from bot")

    user_raw = parsed.get("user")
    user: dict[str, Any] = {}
    if user_raw:
        user = json.loads(user_raw)
    return {
        "telegram_id": int(user.get("id", 0)),
        "username": user.get("username"),
        "first_name": user.get("first_name", ""),
        "last_name": user.get("last_name", ""),
        "raw": parsed,
        "user": user,
    }


def init_data_from_request_headers(headers: Any) -> str:
    """aiohttp: headers.get('X-Telegram-Init-Data') или Authorization: tma <data>"""
    tma = headers.get("X-Telegram-Init-Data")
    if tma:
        return tma
    auth = headers.get("Authorization", "")
    if auth.lower().startswith("tma "):
        return auth[4:].strip()
    return ""
