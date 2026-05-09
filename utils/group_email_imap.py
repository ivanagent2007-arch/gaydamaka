"""Синхронное чтение IMAP (вызывать через asyncio.to_thread)."""

from __future__ import annotations

import email
import html as html_module
import imaplib
import logging
import os
import re
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime

from email import policy

import config

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _raw_bytes_from_fetch(fetch_data) -> bytes | None:
    """Достаёт RFC822 из ответа imaplib (структура у разных серверов чуть отличается)."""
    if not fetch_data:
        return None
    for item in fetch_data:
        if isinstance(item, tuple) and len(item) >= 2:
            payload = item[1]
            if isinstance(payload, bytes) and len(payload) > 0:
                return payload
        if isinstance(item, bytes) and len(item) > 0:
            return item
    return None


def is_valid_email(s: str) -> bool:
    s = (s or "").strip().lower()
    return bool(_EMAIL_RE.match(s))


def normalize_sender(from_header: str) -> str:
    """Адрес отправителя в нижнем регистре."""
    _, addr = parseaddr(from_header or "")
    return addr.strip().lower()


def _decode_str(s: str | None) -> str:
    if not s:
        return ""
    try:
        return str(make_header(decode_header(s)))
    except (TypeError, ValueError):
        return s


# Убираем <style>/<script> целиком — иначе @font-face и пр. попадают в превью.
_STYLE_OR_SCRIPT_BLOCK = re.compile(
    r"<style\b[^>]*>.*?</style>|<script\b[^>]*>.*?</script>",
    re.DOTALL | re.IGNORECASE,
)
_HTML_COMMENTS = re.compile(r"<!--.*?-->", re.DOTALL)
_LINK_TAGS = re.compile(r"<link\b[^>]*>", re.IGNORECASE)


def _normalize_mail_whitespace(text: str) -> str:
    """
    Убирает «простыни» из пустых строк после разборки HTML (блоки → много \\n).
    """
    if not text:
        return ""
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"[\u00a0\u200b\u200c\u200d\ufeff]+", " ", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n[ \t]*\n[ \t]*(?:\n[ \t]*)+", "\n\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    lines = [ln.strip() for ln in t.split("\n")]
    out: list[str] = []
    for ln in lines:
        if ln:
            out.append(ln)
        elif out and out[-1] != "":
            out.append("")
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out).strip()


def _html_to_readable_text(raw_html: str) -> str:
    """HTML-письмо → читаемый текст без CSS/JS и служебных тегов."""
    s = _STYLE_OR_SCRIPT_BLOCK.sub(" ", raw_html)
    s = _HTML_COMMENTS.sub(" ", s)
    s = _LINK_TAGS.sub(" ", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html_module.unescape(s)
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = s.strip()
    s = _strip_leaked_css_noise(s)
    return _normalize_mail_whitespace(s)


def _strip_leaked_css_noise(text: str) -> str:
    """
    Убирает строки, похожие на CSS, если они явно «просочились» в текст
    (кривой multipart, обрезанный <style> и т.п.).
    """
    if not text or text.count("{") < 2:
        return text
    if not any(x in text for x in ("@media", "@font-face", "!important", ".mob_")):
        return text
    keep: list[str] = []
    for line in text.split("\n"):
        t = line.strip()
        if not t:
            keep.append(line)
            continue
        low = t.lower()
        if low.startswith("@media") or low.startswith("@font-face") or low.startswith("@import"):
            continue
        if low.startswith("u+.body") or low.startswith("u + .body"):
            continue
        if re.match(r"^\.[\w\-]+\s*\{", t) or re.match(r"^#[\w\-]+\s*\{", t):
            continue
        if "{" in t and "}" in t and "!important" in t:
            if re.search(
                r"\b(width|height|max-width|min-width|border-radius|display|padding|margin)\s*:",
                low,
            ):
                continue
        keep.append(line)
    return "\n".join(keep)


def _decode_part_payload(part: email.message.Message) -> str | None:
    payload = part.get_payload(decode=True)
    if not payload:
        return None
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _is_attachment_part(part: email.message.Message) -> bool:
    return (part.get_content_disposition() or "").lower() == "attachment"


def _extract_text(msg: email.message.Message) -> str:
    if msg.is_multipart():
        plain: str | None = None
        html: str | None = None
        for part in msg.walk():
            if part.is_multipart():
                continue
            if _is_attachment_part(part):
                continue
            ctype = (part.get_content_type() or "").lower()
            if ctype == "text/plain" and plain is None:
                plain = _decode_part_payload(part)
            elif ctype == "text/html" and html is None:
                html = _decode_part_payload(part)
        # Рассылки часто кладут мусор/CSS в text/plain, а нормальное тело — в text/html.
        if html:
            return _html_to_readable_text(html)
        if plain:
            p = plain.strip()
            if p.lower().startswith("<!doctype") or p.lower().startswith("<html"):
                return _html_to_readable_text(plain)
            if "<table" in p.lower() or "<div" in p.lower():
                return _html_to_readable_text(plain)
            return _normalize_mail_whitespace(_strip_leaked_css_noise(plain))
        return ""
    else:
        payload = msg.get_payload(decode=True)
        if isinstance(payload, bytes):
            charset = msg.get_content_charset() or "utf-8"
            try:
                decoded = payload.decode(charset, errors="replace")
            except LookupError:
                decoded = payload.decode("utf-8", errors="replace")
            if (msg.get_content_type() or "").lower() == "text/html":
                return _html_to_readable_text(decoded)
            return _normalize_mail_whitespace(_strip_leaked_css_noise(decoded))
        if isinstance(payload, str):
            if (msg.get_content_type() or "").lower() == "text/html":
                return _html_to_readable_text(payload)
            return _normalize_mail_whitespace(_strip_leaked_css_noise(payload))
    return ""


def _message_id_from(msg: email.message.Message, uid: str) -> str:
    mid = (msg.get("Message-ID") or "").strip()
    if mid:
        return mid[:512]
    return f"<no-mid-{uid}>"


def _sanitize_filename(name: str) -> str:
    name = (name or "").replace("\x00", "").strip()
    name = os.path.basename(name)
    name = re.sub(r'[<>:"|?*]', "_", name)
    if not name or name in (".", ".."):
        return "file.bin"
    return name[:180]


def _decode_mime_filename(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw)))
    except (TypeError, ValueError):
        return raw


def _extract_attachments(msg: email.message.Message) -> list[dict]:
    """
    Вложения: attachment; inline с именем (кроме text/*); часть с именем файла без disposition (кроме text/*).
    """
    out: list[dict] = []
    max_bytes = max(1, config.EMAIL_ATTACHMENT_MAX_BYTES)
    max_n = max(1, config.EMAIL_ATTACHMENT_MAX_COUNT)

    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = part.get_content_type() or "application/octet-stream"
        disp = (part.get_content_disposition() or "").lower()
        raw_fn = part.get_filename()
        filename = _sanitize_filename(_decode_mime_filename(raw_fn)) if raw_fn else ""

        payload = part.get_payload(decode=True)
        if not payload or not isinstance(payload, (bytes, bytearray)):
            continue

        take = False
        if disp == "attachment":
            take = True
            if not filename:
                sub = part.get_content_subtype() or "bin"
                filename = f"attachment.{sub}"
        elif disp == "inline" and filename:
            take = not ctype.startswith("text/")
        elif filename and not disp:
            if ctype == "text/html":
                continue
            take = True

        if not take:
            continue

        data = bytes(payload)
        if len(data) > max_bytes:
            continue
        if len(data) == 0:
            continue

        out.append({"filename": filename, "data": data, "mime": ctype})
        if len(out) >= max_n:
            break

    return out


def imap_fetch_new(
    host: str,
    port: int,
    use_ssl: bool,
    login: str,
    password: str,
    last_uid: int,
    bootstrapped: bool,
) -> tuple[list[dict], int, bool]:
    """
    Возвращает (список писем, новый last_uid, bootstrapped).
    Пока bootstrapped=False: пустой INBOX → не грузим старые; первый раз с письмами — только max UID;
    после bootstrapped=True — обычная доставка новых (включая первое письмо в ранее пустой ящик).
    """
    if use_ssl:
        M = imaplib.IMAP4_SSL(host, port)
    else:
        M = imaplib.IMAP4(host, port)
    try:
        M.login(login, password)
        M.select("INBOX", readonly=True)

        typ, data = M.uid("SEARCH", None, "ALL")
        if typ != "OK" or not data:
            return [], last_uid, bootstrapped

        raw_uids = data[0].split() if data[0] else []
        if not raw_uids:
            return [], last_uid, True

        uids_int = [int(x) for x in raw_uids]
        max_uid = max(uids_int)

        if not bootstrapped:
            return [], max_uid, True

        out: list[dict] = []
        processed_last = last_uid
        for u in sorted(x for x in uids_int if x > last_uid):
            uid_str = str(u)
            typ, data = M.uid("FETCH", uid_str, "(RFC822)")
            raw_bytes: bytes | None = None
            if typ == "OK" and data:
                raw_bytes = _raw_bytes_from_fetch(data)
            if not raw_bytes:
                typ2, data2 = M.uid("FETCH", uid_str, "(BODY.PEEK[])")
                if typ2 == "OK" and data2:
                    raw_bytes = _raw_bytes_from_fetch(data2)
            if not raw_bytes:
                logger.warning("IMAP FETCH UID %s: нет тела (RFC822/BODY.PEEK)", uid_str)
                processed_last = u
                continue
            try:
                msg = email.message_from_bytes(raw_bytes, policy=policy.default)
            except Exception as ex:
                logger.warning("IMAP FETCH UID %s: разбор MIME: %s", uid_str, ex)
                processed_last = u
                continue
            from_h = _decode_str(msg.get("From"))
            subj = _decode_str(msg.get("Subject"))
            body = _extract_text(msg)[:8000]
            attachments = _extract_attachments(msg)
            msg_id = _message_id_from(msg, uid_str)
            date_str = msg.get("Date")
            received_at = datetime.utcnow()
            if date_str:
                try:
                    received_at = parsedate_to_datetime(date_str)
                    if received_at.tzinfo:
                        received_at = received_at.astimezone(timezone.utc).replace(tzinfo=None)
                except (TypeError, ValueError):
                    pass

            out.append(
                {
                    "uid": u,
                    "message_id": msg_id,
                    "sender_raw": from_h,
                    "sender_norm": normalize_sender(from_h),
                    "subject": subj[:1024],
                    "body_preview": body[:3500],
                    "received_at": received_at,
                    "attachments": attachments,
                }
            )
            processed_last = u

        # last_uid только по успешно разобранным письмам — иначе «пропускали» UID без доставки
        new_last = processed_last
        return out, new_last, True
    finally:
        try:
            M.logout()
        except Exception:
            pass
