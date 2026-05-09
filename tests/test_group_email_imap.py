from email.message import EmailMessage

from utils.group_email_imap import is_valid_email, normalize_sender
from utils import group_email_imap as gei


def test_is_valid_email():
    assert is_valid_email("a@b.co")
    assert not is_valid_email("not-an-email")
    assert not is_valid_email("")


def test_normalize_sender():
    assert normalize_sender("Dekanat <dekanat@fa.ru>") == "dekanat@fa.ru"
    assert normalize_sender("user@MAIL.RU") == "user@mail.ru"


def test_html_to_readable_strips_style_and_script():
    raw = """<html><head><style type="text/css">
@font-face { font-family: 'X'; src: url(https://x/a.woff2); }
</style></head><body><p>Привет из письма</p></body></html>"""
    out = gei._html_to_readable_text(raw)
    assert "font-face" not in out.lower()
    assert "woff2" not in out.lower()
    assert "Привет из письма" in out


def test_extract_text_html_part_prefers_readable():
    msg = EmailMessage()
    msg["Subject"] = "s"
    msg.set_content("<p>plain</p>", subtype="html")
    t = gei._extract_text(msg)
    assert "plain" in t
    assert "<p>" not in t


def test_normalize_mail_whitespace_collapses_blank_runs():
    raw = "Первая строка\n" + "\n" * 80 + "Вторая"
    out = gei._normalize_mail_whitespace(raw)
    assert "Первая строка" in out
    assert "Вторая" in out
    assert out.count("\n") <= 2


def test_multipart_prefers_html_when_plain_has_css():
    """Рассылки: text/plain с @media, нормальный текст в text/html."""
    msg = EmailMessage()
    msg["Subject"] = "s"
    msg.set_content(
        "30 дней за 1 р\n@media x {.mob_100 { width: 100% !important; }}",
        subtype="plain",
        charset="utf-8",
    )
    msg.add_alternative(
        "<html><body><p>Акция тридцать дней</p></body></html>",
        subtype="html",
    )
    t = gei._extract_text(msg)
    assert "Акция тридцать дней" in t
    assert "@media" not in t
    assert "mob_100" not in t


def test_extract_attachments_from_mime():
    msg = EmailMessage()
    msg["Subject"] = "x"
    msg.set_content("Текст письма")
    msg.add_attachment(
        b"%PDF-1.4 fake",
        maintype="application",
        subtype="pdf",
        filename="doc.pdf",
    )
    atts = gei._extract_attachments(msg)
    assert len(atts) == 1
    assert atts[0]["filename"] == "doc.pdf"
    assert atts[0]["data"].startswith(b"%PDF")
