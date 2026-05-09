import config
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, WebAppInfo


def schedule_submenu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="Сегодня"),
                KeyboardButton(text="Завтра"),
            ],
            [
                KeyboardButton(text="Неделя"),
                KeyboardButton(text="Дата"),
            ],
            [KeyboardButton(text="Меню")],
        ],
        resize_keyboard=True,
    )


def main_menu_kb(is_elder: bool, has_group: bool = True) -> ReplyKeyboardMarkup:
    webapp_url = f"{config.WEBAPP_PUBLIC_URL.rstrip('/')}/index.html"
    rows: list[list[KeyboardButton]] = []
    if not has_group:
        rows.append([KeyboardButton(text="Ввести код группы")])
    if has_group:
        rows.append([KeyboardButton(text="Расписание")])
        rows.append([KeyboardButton(text="Состав группы")])
        rows.append(
            [
                KeyboardButton(text="Дни рождения"),
                KeyboardButton(text="Почта"),
            ]
        )
    rows.extend(
        [
            [
                KeyboardButton(
                    text="Мини-приложение",
                    web_app=WebAppInfo(url=webapp_url),
                ),
                KeyboardButton(text="Отметиться на паре"),
            ],
            [KeyboardButton(text="Меню")],
        ]
    )
    if is_elder:
        rows.append(
            [
                KeyboardButton(text="Добавить баллы"),
                KeyboardButton(text="Новый дедлайн"),
            ]
        )
        rows.append([KeyboardButton(text="Добавить ДЗ к паре")])
        rows.append([KeyboardButton(text="Удалить ДЗ")])
        rows.append([KeyboardButton(text="Отчёт посещаемости")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)
