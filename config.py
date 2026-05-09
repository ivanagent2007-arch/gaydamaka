import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ELSTAROST_TELEGRAM_IDS = [
    int(x.strip())
    for x in os.getenv("ELSTAROST_TELEGRAM_IDS", "").split(",")
    if x.strip().isdigit()
]
ELSTAROST_USERNAMES = {
    u.strip().lstrip("@").lower()
    for u in os.getenv("ELSTAROST_USERNAMES", "").split(",")
    if u.strip()
}

GROUP_NAME = os.getenv("GROUP_NAME", "Группа").strip()
RUZ_BASE_URL = os.getenv("RUZ_BASE_URL", "https://ruz.fa.ru").rstrip("/")
RUZ_GROUP_SEARCH = os.getenv("RUZ_GROUP_SEARCH", GROUP_NAME).strip()
RUZ_MAIN_URL = f"{RUZ_BASE_URL}/ruz/main"

# Окно дат для API РУЗ: прошлые и будущие дни относительно сегодня (макс. ~2 года с каждой стороны).
_RUZ_PAST = int(os.getenv("RUZ_SCHEDULE_PAST_DAYS", "400").strip() or "400")
_RUZ_FUTURE = int(os.getenv("RUZ_SCHEDULE_FUTURE_DAYS", "400").strip() or "400")
RUZ_SCHEDULE_PAST_DAYS = max(0, min(_RUZ_PAST, 730))
RUZ_SCHEDULE_FUTURE_DAYS = max(0, min(_RUZ_FUTURE, 730))

# Одна порция запроса к JSON API РУЗ (месяцев ~2); иначе длинный интервал даёт ошибку или таймаут.
_RUZ_CHUNK = int(os.getenv("RUZ_API_CHUNK_DAYS", "56").strip() or "56")
RUZ_API_CHUNK_DAYS = max(14, min(_RUZ_CHUNK, 120))

WEBAPP_HOST = os.getenv("WEBAPP_HOST", "0.0.0.0")
WEBAPP_PORT = int(os.getenv("WEBAPP_PORT", "8080"))

_webapp_raw = os.getenv("WEBAPP_PUBLIC_URL", "").strip().rstrip("/")
# Пусто → локальная разработка (Telegram с телефона не откроет без туннеля)
WEBAPP_PUBLIC_URL = (
    _webapp_raw if _webapp_raw else f"http://127.0.0.1:{WEBAPP_PORT}"
)

_raw_db_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data/bot.db").strip()
# Railway Postgres часто отдаёт URL в формате postgres://...;
# для SQLAlchemy async нужен postgresql+asyncpg://...
if _raw_db_url.startswith("postgres://"):
    _raw_db_url = _raw_db_url.replace("postgres://", "postgresql+asyncpg://", 1)
elif _raw_db_url.startswith("postgresql://") and "+asyncpg" not in _raw_db_url.split("://", 1)[0]:
    _raw_db_url = _raw_db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
DATABASE_URL = _raw_db_url
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", str(BASE_DIR / "data" / "uploads")))
TZ = os.getenv("TZ", "Europe/Moscow")

# Один файл ДЗ (бот + мини-приложение), мегабайт (целое 1…200)
_HOMEWORK_UPLOAD_MAX_MB = int(os.getenv("HOMEWORK_UPLOAD_MAX_MB", "20").strip() or "20")
HOMEWORK_UPLOAD_MAX_MB = max(1, min(_HOMEWORK_UPLOAD_MAX_MB, 200))
HOMEWORK_UPLOAD_MAX_BYTES = HOMEWORK_UPLOAD_MAX_MB * 1024 * 1024

# IMAP для корпоративной почты группы (чтение входящих).
# Пустая строка = хост подбирается по домену ящика (yandex → imap.yandex.ru и т.д.)
IMAP_HOST = os.getenv("IMAP_HOST", "").strip()
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
IMAP_USE_SSL = os.getenv("IMAP_USE_SSL", "1").strip().lower() in ("1", "true", "yes")


def resolve_imap_host(login_email: str) -> str:
    """IMAP-сервер: из .env или авто по домену ящика (см. utils.imap_hosts)."""
    from utils.imap_hosts import resolve_imap_host_for_email

    return resolve_imap_host_for_email(login_email, IMAP_HOST)


# Вложения из писем → Telegram (лимит бота ~50 МБ на файл)
EMAIL_ATTACHMENT_MAX_BYTES = int(os.getenv("EMAIL_ATTACHMENT_MAX_BYTES", str(48 * 1024 * 1024)))
EMAIL_ATTACHMENT_MAX_COUNT = int(os.getenv("EMAIL_ATTACHMENT_MAX_COUNT", "15"))


def is_elder(telegram_id: int, username: str | None) -> bool:
    if telegram_id in ELSTAROST_TELEGRAM_IDS:
        return True
    if username:
        if username.lower() in ELSTAROST_USERNAMES:
            return True
    return False


def validate_config() -> None:
    if not BOT_TOKEN or BOT_TOKEN == "your_bot_token_here":
        raise RuntimeError("Укажи BOT_TOKEN в .env")
    raw = os.getenv("WEBAPP_PUBLIC_URL", "").strip()
    if raw and "your-domain.example" in raw.lower():
        raise RuntimeError(
            "В .env в WEBAPP_PUBLIC_URL указана заглушка your-domain.example — "
            "мини-приложение в Telegram не откроется.\n\n"
            "Сделайте так:\n"
            "1) На сервере запустите туннель (на shared-хостинге без QUIC): "
            "cloudflared tunnel --url http://localhost:9090 --protocol http2\n"
            "2) Скопируйте выданный https://….trycloudflare.com в WEBAPP_PUBLIC_URL (без слэша в конце).\n"
            "3) Перезапустите бота.\n"
            "4) В @BotFather → ваш бот → Bot Settings → Menu Button укажите тот же базовый URL + /index.html\n\n"
            "Пока не настроили туннель — закомментируйте WEBAPP_PUBLIC_URL в .env (бот поднимется для тестов без Web App)."
        )
