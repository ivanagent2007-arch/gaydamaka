import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BotCommand, MenuButtonCommands
from aiohttp import web

import config
from database import init_db
from handlers import register_handlers
from middleware.db_session import DbSessionMiddleware
from middleware.role_check import RoleMiddleware
from utils.fsm_storage import SqlAlchemyStorage
from utils.scheduler import setup_scheduler
from web_server import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


def _log_webapp_hints() -> None:
    """Telegram открывает Mini App только по HTTPS; кнопки в боте используют WEBAPP_PUBLIC_URL."""
    url = config.WEBAPP_PUBLIC_URL
    if not url.lower().startswith("https://"):
        logging.warning(
            "WEBAPP_PUBLIC_URL должен быть HTTPS (ngrok, cloudflared, свой домен). "
            "Иначе кнопка «Мини-приложение» в Telegram не откроется. Сейчас: %s",
            url,
        )
    else:
        logging.info(
            "Mini App для Telegram: %s/index.html (тот же URL в BotFather → Menu Button)",
            url.rstrip("/"),
        )


async def main() -> None:
    config.validate_config()
    _log_webapp_hints()
    await init_db()

    if sys.platform == "win32":
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    bot = Bot(token=config.BOT_TOKEN)
    await bot.set_my_commands([
        BotCommand(command="start", description="Регистрация / перезапуск"),
        BotCommand(command="menu", description="Главное меню"),
        BotCommand(command="help", description="Список команд"),
        BotCommand(command="schedule", description="Расписание"),
        BotCommand(command="my_grades", description="Баллы по семестрам"),
        BotCommand(command="setup_org", description="[Староста] Привязать org.fa.ru"),
        BotCommand(command="sync_site_grades", description="[Староста] Загрузить баллы с сайта"),
        BotCommand(command="birthdays", description="Дни рождения"),
        BotCommand(command="mail", description="Почта группы"),
        BotCommand(command="group_members", description="Состав группы"),
        BotCommand(command="versions", description="История обновлений"),
    ])
    # Менюшка слева от поля ввода — это «команды», а не Mini App.
    # Открывать мини-приложение нужно кнопкой справа от поля ввода ("Мини-приложение"
    # из главной reply-клавиатуры). Этот вызов перекрывает любую настройку Menu Button
    # из BotFather для всех пользователей бота.
    try:
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    except TelegramAPIError as ex:
        logging.warning("Не удалось переключить menu button на «Команды»: %s", ex)
    # Персистентное FSM-хранилище: диалоги (создание группы, ввод дедлайна, загрузка ДЗ)
    # переживают рестарт бота — состояние пишется в ту же БД, что и остальные данные.
    dp = Dispatcher(storage=SqlAlchemyStorage())
    dp.update.middleware(DbSessionMiddleware())
    dp.update.middleware(RoleMiddleware())
    register_handlers(dp)

    sched = setup_scheduler(bot)
    sched.start()

    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, config.WEBAPP_HOST, config.WEBAPP_PORT)
    await site.start()
    logging.info(
        "HTTP-сервер мини-приложения: http://%s:%s (снаружи — туннель HTTPS → этот порт)",
        config.WEBAPP_HOST,
        config.WEBAPP_PORT,
    )

    try:
        await dp.start_polling(bot)
    finally:
        sched.shutdown(wait=False)
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
