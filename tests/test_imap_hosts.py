import pytest

from utils.imap_hosts import resolve_imap_host_for_email


@pytest.mark.parametrize(
    "email,expected",
    [
        ("user@yandex.ru", "imap.yandex.ru"),
        ("u@ya.ru", "imap.yandex.ru"),
        ("g@gmail.com", "imap.gmail.com"),
        ("m@mail.ru", "imap.mail.ru"),
        ("x@outlook.com", "outlook.office365.com"),
        ("x@hotmail.com", "outlook.office365.com"),
        ("t@school.onmicrosoft.com", "outlook.office365.com"),
        ("y@yahoo.com", "imap.mail.yahoo.com"),
        ("y@yahoo.co.uk", "imap.mail.yahoo.com"),
        ("i@icloud.com", "imap.mail.me.com"),
        ("a@gmx.de", "imap.gmx.net"),
        ("b@rambler.ru", "imap.rambler.ru"),
        ("c@ukr.net", "imap.ukr.net"),
        ("d@custom.edu", "imap.custom.edu"),
    ],
)
def test_resolve_by_domain(email: str, expected: str) -> None:
    assert resolve_imap_host_for_email(email, "") == expected


def test_env_override() -> None:
    assert (
        resolve_imap_host_for_email("any@yandex.ru", "imap.example.com")
        == "imap.example.com"
    )
