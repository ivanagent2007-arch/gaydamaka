import html
import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import default_state
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup, WebAppInfo

import config
from database import User, UserRole
from handlers.states import OnboardingStates
from keyboards.inline import onboarding_kb
from keyboards.reply import main_menu_kb
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from utils.birthday_helpers import parse_birthday_text
from utils.group_context import get_study_group
from utils.user_roles import effective_is_elder

logger = logging.getLogger(__name__)
router = Router(name="common")

CHANGELOG = [
    ("1.3.1", "14.04.2026", "Староста: удаление прикреплённого ДЗ (бот, мини-приложение)"),
    ("1.3.0", "14.04.2026", "Состав группы и исключение студентов старостой; мини-приложение «Состав»"),
    ("1.2.0", "09.04.2026", "Поддержка нескольких сайтов РУЗ (ГУЗ, ФА и др.); новое приветствие"),
    ("1.1.0", "08.04.2026", "Парсинг баллов с org.fa.ru — /setup_org, /sync_site_grades"),
    ("1.0.0", "07.04.2026", "Запуск бота на хостинге, базовый функционал"),
]

_GREETING = (
    "Привет! Я — <b>бот-помощник для учебных групп</b>.\n"
    "\n"
    "Вот что я умею:\n"
    "  — Расписание занятий с сайта РУЗ\n"
    "  — Учёт баллов и оценок\n"
    "  — Домашние задания к парам\n"
    "  — Напоминания о дедлайнах\n"
    "  — Дни рождения одногруппников\n"
    "  — Корпоративная почта группы\n"
    "  — Тайный Санта\n"
    "\n"
    "Давай познакомимся! Для начала напиши своё <b>ФИО</b>\n"
    "(например: <code>Иванов Иван Иванович</code>)."
)

_WELCOME_BACK_NO_BIRTHDAY = (
    "С возвращением, <b>{name}</b>!\n"
    "\n"
    "У меня ещё не записан твой день рождения.\n"
    "Укажи его в формате <b>ДД.ММ</b> (например <code>15.03</code>)\n"
    "или <b>ДД.ММ.ГГГГ</b> (например <code>15.03.2003</code>)."
)


def _esc(s: str) -> str:
    return html.escape(s or "", quote=False)


def _format_changelog() -> str:
    lines = ["<b>История обновлений</b>\n"]
    for ver, dt, desc in CHANGELOG:
        lines.append(f"<b>v{ver}</b>  ({dt})\n— {desc}\n")
    return "\n".join(lines)


def _needs_full_name(u: User) -> bool:
    name = (u.full_name or "").strip()
    return not name


def _needs_birthday(u: User) -> bool:
    return u.birthday_month is None or u.birthday_day is None


async def send_main_welcome(
    message: Message, session, state: FSMContext, db_user: User | None
) -> None:
    await state.clear()
    uid = message.from_user.id
    uname = message.from_user.username
    full = _esc((db_user.full_name if db_user else None) or message.from_user.full_name or "")
    show_elder = effective_is_elder(db_user, uid, uname)
    has_group = bool(db_user and db_user.study_group_id)
    sg = await get_study_group(session, db_user)
    group_line = f"Группа: <b>{_esc(sg.name)}</b>" if sg else "Группа: <i>не выбрана</i>"
    role_line = "староста" if show_elder else "студент"

    text = (
        f"Привет, <b>{full}</b>!\n"
        f"{group_line}\n"
        f"Роль: {role_line}\n"
        "\n"
        "Команды: /help  /menu  /webapp"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=main_menu_kb(show_elder, has_group))

    if not has_group:
        await message.answer(
            "Ты ещё не в учебной группе.\n"
            "Выбери свою роль — <b>староста</b> создаёт группу и получает код, "
            "а <b>студент</b> вводит этот код.",
            parse_mode="HTML",
            reply_markup=onboarding_kb(),
        )


def _menu_flags(db_user: User | None, is_elder: bool) -> tuple[bool, bool]:
    has_group = bool(db_user and db_user.study_group_id)
    return is_elder, has_group


# ──────────────────────────────────────────────────────
#  /start  —  точка входа
# ──────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, session, state: FSMContext) -> None:
    uid = message.from_user.id
    uname = message.from_user.username
    role = UserRole.elder if config.is_elder(uid, uname) else UserRole.student

    try:
        db_user = await session.scalar(select(User).where(User.telegram_id == uid))
        is_new = db_user is None

        if is_new:
            db_user = User(telegram_id=uid, full_name="", group_name="", role=role)
            session.add(db_user)
            try:
                await session.flush()
            except IntegrityError:
                # Юзер мог остаться от упавшей предыдущей попытки /start (старый баг с
                # «database is locked» в SQLite). Откатываемся и перечитываем существующего.
                await session.rollback()
                db_user = await session.scalar(
                    select(User).where(User.telegram_id == uid)
                )
                if db_user is None:
                    raise  # должно было быть — что-то реально не так
            else:
                db_user = await session.scalar(
                    select(User).where(User.telegram_id == uid)
                )
        else:
            if role == UserRole.elder:
                db_user.role = UserRole.elder
                await session.flush()
    except Exception as ex:
        # До этой правки exception тут проглатывался DbSessionMiddleware (rollback + raise),
        # юзер видел тишину. Теперь хотя бы пишем в лог и отвечаем явно.
        logger.exception(
            "cmd_start: ошибка регистрации пользователя tg_id=%s username=%s: %s",
            uid, uname, ex,
        )
        await message.answer(
            "⚠️ Не удалось завершить регистрацию. Это серверная ошибка — попроси "
            "разработчика посмотреть логи. Текст ошибки для отладки:\n"
            f"<code>{_esc(ex.__class__.__name__)}: {_esc(str(ex))[:300]}</code>",
            parse_mode="HTML",
        )
        return

    try:
        if _needs_full_name(db_user):
            await state.set_state(OnboardingStates.full_name)
            await message.answer(_GREETING, parse_mode="HTML")
            return

        if _needs_birthday(db_user):
            await state.set_state(OnboardingStates.birthday)
            await message.answer(
                _WELCOME_BACK_NO_BIRTHDAY.format(name=_esc(db_user.full_name)),
                parse_mode="HTML",
            )
            return

        await send_main_welcome(message, session, state, db_user)
    except Exception as ex:
        logger.exception(
            "cmd_start: ошибка после регистрации tg_id=%s: %s", uid, ex
        )
        await message.answer(
            "⚠️ Ошибка при открытии меню. Логи:\n"
            f"<code>{_esc(ex.__class__.__name__)}: {_esc(str(ex))[:300]}</code>",
            parse_mode="HTML",
        )


# ──────────────────────────────────────────────────────
#  Онбординг: шаг 1 — ФИО
# ──────────────────────────────────────────────────────

@router.message(OnboardingStates.full_name, F.text == "Меню")
async def onboard_name_menu_escape(
    message: Message, state: FSMContext, session, db_user: User | None, is_elder: bool,
) -> None:
    await cmd_menu(message, is_elder, db_user, state)


@router.message(OnboardingStates.full_name, F.text & ~F.text.startswith("/"))
async def onboard_full_name(message: Message, session, state: FSMContext) -> None:
    uid = message.from_user.id
    raw = (message.text or "").strip()

    if len(raw) < 2:
        await message.answer("Слишком короткое имя. Напиши полное ФИО, например: <code>Иванов Иван Иванович</code>", parse_mode="HTML")
        return
    if len(raw) > 200:
        await message.answer("Слишком длинное — сократи до 200 символов.")
        return

    db_user = await session.scalar(select(User).where(User.telegram_id == uid))
    if not db_user:
        await state.clear()
        await message.answer("Что-то пошло не так. Попробуй /start ещё раз.")
        return

    db_user.full_name = raw
    await session.flush()

    if _needs_birthday(db_user):
        await state.set_state(OnboardingStates.birthday)
        await message.answer(
            f"Приятно познакомиться, <b>{_esc(raw)}</b>!\n"
            "\n"
            "Теперь укажи свой <b>день рождения</b> — я буду поздравлять тебя\n"
            "и напоминать одногруппникам.\n"
            "\n"
            "Формат: <b>ДД.ММ</b> (например <code>15.03</code>)\n"
            "или <b>ДД.ММ.ГГГГ</b> (например <code>15.03.2003</code>).",
            parse_mode="HTML",
        )
        return

    await _finish_onboarding(message, session, state, db_user)


@router.message(OnboardingStates.full_name, ~F.text)
async def onboard_name_need_text(message: Message) -> None:
    await message.answer("Напиши ФИО обычным текстовым сообщением.")


# ──────────────────────────────────────────────────────
#  Онбординг: шаг 2 — День рождения
# ──────────────────────────────────────────────────────

@router.message(OnboardingStates.birthday, F.text == "Меню")
async def onboard_bday_menu_escape(
    message: Message, state: FSMContext, session, db_user: User | None, is_elder: bool,
) -> None:
    await cmd_menu(message, is_elder, db_user, state)


@router.message(OnboardingStates.birthday, F.text & ~F.text.startswith("/"))
async def onboard_birthday(message: Message, session, state: FSMContext) -> None:
    uid = message.from_user.id
    parsed = parse_birthday_text(message.text or "")
    if not parsed:
        await message.answer(
            "Не понял дату. Пример: <code>15.03</code> или <code>15.03.2003</code>",
            parse_mode="HTML",
        )
        return

    month, day, year = parsed
    db_user = await session.scalar(select(User).where(User.telegram_id == uid))
    if not db_user:
        await state.clear()
        await message.answer("Что-то пошло не так. Попробуй /start ещё раз.")
        return

    db_user.birthday_month = month
    db_user.birthday_day = day
    db_user.birth_year = year
    await session.flush()

    await _finish_onboarding(message, session, state, db_user)


@router.message(OnboardingStates.birthday, ~F.text)
async def onboard_bday_need_text(message: Message) -> None:
    await message.answer(
        "Пришли день рождения текстом — формат <b>ДД.ММ</b> или <b>ДД.ММ.ГГГГ</b>.",
        parse_mode="HTML",
    )


# ──────────────────────────────────────────────────────
#  Онбординг: финал — роль и группа
# ──────────────────────────────────────────────────────

async def _finish_onboarding(
    message: Message, session, state: FSMContext, db_user: User,
) -> None:
    await state.clear()
    uid = message.from_user.id
    uname = message.from_user.username
    show_elder = effective_is_elder(db_user, uid, uname)
    has_group = bool(db_user.study_group_id)

    if has_group:
        sg = await get_study_group(session, db_user)
        group_name = sg.name if sg else "?"
        await message.answer(
            f"Отлично! Ты уже в группе <b>{_esc(group_name)}</b>.\n"
            "Всё готово к работе!",
            parse_mode="HTML",
            reply_markup=main_menu_kb(show_elder, True),
        )
        return

    await message.answer(
        "Отлично, данные сохранены!\n"
        "\n"
        "Последний шаг — <b>учебная группа</b>.\n"
        "Выбери свою роль:",
        parse_mode="HTML",
        reply_markup=onboarding_kb(),
    )


# ──────────────────────────────────────────────────────
#  Стандартные команды
# ──────────────────────────────────────────────────────

@router.message(Command("help"))
async def cmd_help(message: Message, is_elder: bool) -> None:
    text = (
        "<b>Основные команды:</b>\n"
        "/start — регистрация / приветствие\n"
        "/menu — главное меню\n"
        "/help — эта справка\n"
        "\n"
        "<b>Расписание:</b>\n"
        "/schedule — расписание на сегодня\n"
        "/schedule_week — расписание на неделю\n"
        "Кнопка «Расписание» — выбор: сегодня, завтра, неделя, дата\n"
        "\n"
        "<b>Группа:</b>\n"
        "/groups или «Ввести код группы» — вступить в группу\n"
        "/join КОД — вступить одной строкой\n"
        "/my_group — информация о группе\n"
        "/group_members — состав группы\n"
        "/leave_group — выйти из группы\n"
        "\n"
        "<b>Баллы и задания:</b>\n"
        "/my_grades — мои баллы по семестрам\n"
        "/mark_attendance — отметиться на паре\n"
        "\n"
        "<b>Другое:</b>\n"
        "/birthdays — дни рождения одногруппников\n"
        "/mail — корпоративная почта\n"
        "/mail_now — проверить почту сейчас\n"
        "/webapp — мини-приложение\n"
        "/versions — история обновлений\n"
    )
    if is_elder:
        text += (
            "\n<b>Команды старосты:</b>\n"
            "/create_group — создать учебную группу\n"
            "/update_schedule — обновить расписание из РУЗ\n"
            "/set_deadline — новый дедлайн\n"
            "/start_santa — тайный Санта\n"
            "/setup_org — привязать org.fa.ru\n"
            "/sync_site_grades — загрузить баллы с org.fa.ru\n"
            "\nКнопка «Отчёт посещаемости» — посещаемость за выбранный день.\n"
            "Кнопка «Удалить ДЗ» — снять прикреплённое ДЗ к паре (и дедлайн к ней).\n"
            "/group_members — состав и исключение студентов.\n"
        )
    await message.answer(text, parse_mode="HTML")


@router.message(Command("menu"))
async def cmd_menu(
    message: Message, is_elder: bool, db_user: User | None, state: FSMContext
) -> None:
    await state.clear()
    _, has_group = _menu_flags(db_user, is_elder)
    await message.answer("Главное меню:", reply_markup=main_menu_kb(is_elder, has_group))


@router.message(Command("webapp"))
async def cmd_webapp(message: Message) -> None:
    url = config.WEBAPP_PUBLIC_URL
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="Открыть приложение",
                    web_app=WebAppInfo(url=f"{url}/index.html"),
                )
            ]
        ],
        resize_keyboard=True,
    )
    await message.answer(
        "Нажми кнопку, чтобы открыть мини-приложение (нужен HTTPS в продакшене).",
        reply_markup=kb,
    )


@router.message(F.text == "Мини-приложение", StateFilter(default_state))
async def text_webapp_btn(message: Message) -> None:
    await cmd_webapp(message)


@router.message(F.text == "Отметиться на паре", StateFilter(default_state))
async def text_mark_btn(message: Message, session, db_user: User | None) -> None:
    from handlers.attendance import send_mark_keyboard

    await send_mark_keyboard(message, session, db_user)


@router.message(Command("versions"))
async def cmd_versions(message: Message) -> None:
    await message.answer(_format_changelog(), parse_mode="HTML")


@router.message(F.text == "Меню")
async def text_menu_btn(
    message: Message, is_elder: bool, db_user: User | None, state: FSMContext
) -> None:
    """Срабатывает из любого FSM-состояния — главный способ выйти из сценариев."""
    await cmd_menu(message, is_elder, db_user, state)
