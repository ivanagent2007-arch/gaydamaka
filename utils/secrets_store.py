"""Прозрачное шифрование секретов в БД (IMAP-пароли, cookies org.fa.ru).

Зашифровано хранится с префиксом ``enc:v1:``. Если префикса нет — значение читается как
plaintext (наследие старой схемы); первая же запись заменит его на шифр.

Ключ: ``SECRET_ENCRYPTION_KEY`` из ``.env`` (если задан), иначе детерминированно
выводится из ``BOT_TOKEN`` через HKDF. Это значит, что **смена токена бота приведёт
к тому, что сохранённые секреты не расшифруются** — староста должен будет заново
ввести их (cookies и IMAP-пароли).
"""

from __future__ import annotations

import base64
import hashlib
import logging
from typing import Final

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

import config

logger = logging.getLogger(__name__)

_ENC_PREFIX: Final[str] = "enc:v1:"


def _derive_fernet_key() -> bytes:
    raw = (
        getattr(config, "SECRET_ENCRYPTION_KEY", "")
        or config.BOT_TOKEN
        or ""
    ).encode("utf-8")
    if not raw:
        # Без секрета шифровать смысла нет — но и падать на старте бота не хочется.
        # Возвращаем детерминированный ключ из константы; такие данные не будут защищены,
        # зато код работает. В проде BOT_TOKEN всегда задан.
        raw = b"student-assistant-bot-fallback"
    salt = b"student-assistant-bot/v1"
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=b"secrets-at-rest",
    )
    key = hkdf.derive(raw)
    return base64.urlsafe_b64encode(key)


_fernet: Fernet | None = None


def _cipher() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_derive_fernet_key())
    return _fernet


def encrypt_secret(plain: str | None) -> str | None:
    """Зашифровать значение. ``None`` / пусто возвращается как есть."""
    if plain is None or plain == "":
        return plain
    token = _cipher().encrypt(plain.encode("utf-8")).decode("ascii")
    return f"{_ENC_PREFIX}{token}"


def decrypt_secret(stored: str | None) -> str | None:
    """Расшифровать значение. Если без префикса — вернуть как есть (старые plaintext-записи)."""
    if stored is None or stored == "":
        return stored
    if not stored.startswith(_ENC_PREFIX):
        return stored
    payload = stored[len(_ENC_PREFIX):]
    try:
        return _cipher().decrypt(payload.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        logger.warning(
            "secrets_store: не удалось расшифровать значение (ключ сменился?). "
            "Староста должен переввести IMAP-пароль / cookies org.fa.ru."
        )
        return None


def looks_encrypted(stored: str | None) -> bool:
    return bool(stored) and stored.startswith(_ENC_PREFIX)
