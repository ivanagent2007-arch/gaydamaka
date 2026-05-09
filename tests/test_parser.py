import datetime

from utils.parser import build_schedule_rows, parse_schedule_item


def test_parse_schedule_item_minimal():
    item = {
        "date": "24.03.2026",
        "beginLesson": "09:00",
        "endLesson": "10:30",
        "discipline": "Тест",
        "lecturer": "Иванов",
        "auditorium": "101",
    }
    row = parse_schedule_item(item, "Гр-1", 1)
    assert row is not None
    assert row["subject"] == "Тест"
    assert row["teacher"] == "Иванов"
    assert row["room"] == "101"
    assert row.get("lesson_kind") == ""


def test_parse_schedule_item_kind_of_work():
    item = {
        "date": "24.03.2026",
        "beginLesson": "09:00",
        "endLesson": "10:30",
        "discipline": "Экономика",
        "kindOfWork": "Лекция",
        "lecturer": "Иванов",
        "auditorium": "101",
    }
    row = parse_schedule_item(item, "Гр-1", 1)
    assert row is not None
    assert row["lesson_kind"] == "лекция"


def test_parse_schedule_item_seminar():
    item = {
        "date": "24.03.2026",
        "beginLesson": "11:00",
        "endLesson": "12:30",
        "discipline": "Экономика",
        "kindOfWork": "Семинар",
        "lecturer": "Петров",
        "auditorium": "202",
    }
    row = parse_schedule_item(item, "Гр-1", 1)
    assert row is not None
    assert row["lesson_kind"] == "семинар"


def test_build_schedule_orders_by_day():
    items = [
        {"date": "25.03.2026", "beginLesson": "10:00", "discipline": "B"},
        {"date": "24.03.2026", "beginLesson": "09:00", "discipline": "A"},
    ]
    rows = build_schedule_rows("Гр", items)
    assert len(rows) == 2
    assert rows[0]["lesson_date"] == datetime.date(2026, 3, 24)
