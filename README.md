# Помощник старосты (aiogram 3 + Web App + SQLite)

**Нужен Python 3.11 или 3.12.** На **Python 3.14** зависимости (в частности `pydantic-core` для aiogram) часто не ставятся без сборки из исходников и Rust — проще использовать 3.12 с [python.org](https://www.python.org/downloads/).

Telegram-бот для учебной группы: расписание из [РУЗ Финуниверситета](https://ruz.fa.ru/ruz/main), баллы, ДЗ с файлами, посещаемость, дедлайны с напоминаниями, тайный Санта, мини-приложение.

## Быстрый старт

1. Скопируй `.env.example` в `.env`.
2. Укажи `BOT_TOKEN`.
3. Укажи **числовой** Telegram ID старосты в `ELSTAROST_TELEGRAM_IDS` (например через [@userinfobot](https://t.me/userinfobot)). Username `v9rsh1nk4` уже можно добавить в `ELSTAROST_USERNAMES` — так права сработают и по @username.
4. Настрой `GROUP_NAME` и `RUZ_GROUP_SEARCH` (как группа ищется в РУЗ).
5. Для мини-приложения в продакшене нужен **HTTPS**. Укажи публичный URL в `WEBAPP_PUBLIC_URL` (тот же хост/порт, что доступен из интернета, например через ngrok). Локально для тестов можно оставить `http://127.0.0.1:8080`, но Telegram Web App с телефона до твоего ПК не достучится без туннеля.

```powershell
cd student_assistant_bot
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

В [@BotFather](https://t.me/BotFather) задай Menu Button URL = `{WEBAPP_PUBLIC_URL}/index.html`.

## Команды

| Команда | Кто |
|--------|-----|
| `/start`, `/help`, `/menu`, `/webapp` | все |
| `/schedule`, `/mark_attendance` | все |
| `/update_schedule` | староста |
| `/set_deadline`, кнопки ДЗ/баллы/дедлайн | староста |
| `/start_santa` | староста |

## Планировщик

- Ежедневно в **21:00** (часовой пояс `TZ`, по умолчанию `Europe/Moscow`) старосте уходит сводка посещаемости за день.
- Каждые **15 минут** проверяются дедлайны: за ~24 часа до срока группе уходит уведомление (один раз на дедлайн).

## Тесты

```powershell
pip install pytest
pytest tests
```

## Замечания по РУЗ

Парсер использует официальный JSON API `ruz.fa.ru` (как в проекте [fa_api](https://github.com/GeorgiyDemo/fa_api)). BeautifulSoup задействован в `utils/parser.py` как запасной разбор HTML главной страницы, если API недоступен.

## Структура

Соответствует заданию: `main.py`, `config.py`, `database.py`, `handlers/`, `keyboards/`, `middleware/`, `web_app/`, `utils/`, `requirements.txt`, `.env.example`.
