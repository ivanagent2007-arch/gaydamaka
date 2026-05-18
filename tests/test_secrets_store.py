"""Проверка прозрачного шифрования секретов (Fernet + префикс enc:v1:)."""

import os

import config
from utils.secrets_store import (
    _ENC_PREFIX,
    decrypt_secret,
    encrypt_secret,
    looks_encrypted,
)


def test_encrypt_decrypt_roundtrip():
    plain = "very-long-imap-password-with-symbols-!@#$%^&*()"
    token = encrypt_secret(plain)
    assert token is not None
    assert token.startswith(_ENC_PREFIX)
    assert looks_encrypted(token)
    assert decrypt_secret(token) == plain


def test_empty_and_none_passthrough():
    assert encrypt_secret(None) is None
    assert encrypt_secret("") == ""
    assert decrypt_secret(None) is None
    assert decrypt_secret("") == ""


def test_legacy_plaintext_passes_through_on_read():
    """До миграции в БД были plaintext-значения без префикса — decrypt должен
    возвращать их как есть, чтобы старые данные не пропали."""
    assert decrypt_secret("plain_imap_password") == "plain_imap_password"
    assert decrypt_secret("k=v; k2=v2") == "k=v; k2=v2"
    assert not looks_encrypted("plaintext")


def test_unicode_secrets_roundtrip():
    plain = "Пароль с кириллицей и эмодзи 🐱"
    token = encrypt_secret(plain)
    assert decrypt_secret(token) == plain


def test_corrupted_token_returns_none_not_raise():
    """Если ключ сменился (или данные битые) — decrypt не должен валить процесс."""
    bad = _ENC_PREFIX + "obviously-not-a-fernet-token"
    assert decrypt_secret(bad) is None


def test_each_encryption_produces_different_ciphertext():
    """Fernet вставляет рандомный nonce — один и тот же plaintext шифруется
    разными байтами каждый раз. Защищает от анализа повторяющихся значений."""
    plain = "same_value"
    t1 = encrypt_secret(plain)
    t2 = encrypt_secret(plain)
    assert t1 != t2
    assert decrypt_secret(t1) == decrypt_secret(t2) == plain
