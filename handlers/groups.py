import secrets
import string

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, StateFilter
from aiogram.fsm.state import default_state
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from sqlalchemy import select

import config
from database import GroupEmailPollState, StudyGroup, User, UserRole
from handlers.states import CreateGroupStates, JoinCodeStates
from keyboards.reply import main_menu_kb
from utils.group_context import get_study_group, ruz_search_for_group
from utils.group_email_imap import is_valid_email
from utils.group_roster import user_leave_group
from utils.user_roles import effective_is_elder

RUZ_SOURCES: dict[str, str] = {
    "Финуниверситет (ruz.fa.ru)": "https://ruz.fa.ru",
    "ГУЗ (ruz.guz.ru)": "https://ruz.guz.ru",
}


def _ruz_source_kb() -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(text=label)] for label in RUZ_SOURCES]
    rows.append([KeyboardButton(text="Другой URL")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)

router = Router(name="groups")


def _gen_join_code() -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(8))


def _is_elder_user(u: User) -> bool:
    return (
        u.role in (UserRole.elder, UserRole.deputy_elder)
        or config.is_elder(u.telegram_id, None)
    )


async def _prompt_join_code(message: Message, state: FSMContext) -> None:
    await state.set_state(JoinCodeStates.code)
    await message.answer(
        "Введи код группы — его присылает староста после создания группы в боте.\n"
        "Латиница и цифры, как в сообщении старосты.\n"
        "Можно так же: /join ТВОЙКОД"
    )


@router.callback_query(F.data == "onboard:elder")
async def cb_onboard_elder(
    query: CallbackQuery,
    state: FSMContext,
    session,
    db_user: User | None,
) -> None:
    if not db_user:
        await query.answer("Сначала нажми /start", show_alert=True)
        return
    if db_user.study_group_id:
        await query.answer("Ты уже в учебной группе.", show_alert=True)
        return

    db_user.role = UserRole.elder
    await state.set_state(CreateGroupStates.name)
    if query.message:
        await query.message.answer(
            "Введите название учебной группы (как она будет отображаться у студентов).",
            reply_markup=main_menu_kb(_is_elder_user(db_user), False),
        )
    await query.answer()


@router.callback_query(F.data == "onboard:student")
async def cb_onboard_student(
    query: CallbackQuery, state: FSMContext, session, db_user: User | None
) -> None:
    if not db_user:
        await query.answer("Сначала нажми /start", show_alert=True)
        return
    if db_user.study_group_id:
        await query.answer("Ты уже в учебной группе.", show_alert=True)
        return

    uid = query.from_user.id
    uname = query.from_user.username
    if not config.is_elder(uid, uname):
        db_user.role = UserRole.student

    if query.message:
        await query.message.answer(
            "Главное меню:",
            reply_markup=main_menu_kb(_is_elder_user(db_user), False),
        )
        await _prompt_join_code(query.message, state)
    await query.answer()


@router.message(Command("groups"))
@router.message(F.text == "Ввести код группы", StateFilter(default_state))
@router.message(F.text == "Выбрать группу", StateFilter(default_state))
async def cmd_groups(
    message: Message, state: FSMContext, session, db_user: User | None
) -> None:
    await state.clear()
    if db_user and db_user.study_group_id:
        sg = await get_study_group(session, db_user)
        n = sg.name if sg else "?"
        await message.answer(
            f"Ты уже в группе: {n}.\nЧтобы сменить — сначала /leave_group"
        )
        return
    await _prompt_join_code(message, state)


@router.message(Command("join"))
async def cmd_join(message: Message, state: FSMContext, session, db_user: User | None) -> None:
    await state.clear()
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) > 1 and parts[1].strip():
        code = parts[1].strip().upper()
        await _join_by_code(message, session, db_user, code)
        return
    await state.set_state(JoinCodeStates.code)
    await message.answer("Введи код группы (как дал староста):")


@router.message(Command("leave_group"))
async def cmd_leave_group(
    message: Message, state: FSMContext, session, db_user: User | None, is_elder: bool
) -> None:
    await state.clear()
    if not db_user or not db_user.study_group_id:
        await message.answer("Ты ни в какой группе не состоишь.")
        return
    ok, msg = await user_leave_group(session, db_user)
    if not ok:
        # Староста с участниками: msg уже содержит подсказку «сначала передай роль».
        # Передача роли — в мини-приложении → «Состав группы».
        await message.answer(
            msg + "\n\nПередать роль можно в мини-приложении → «Состав группы»."
        )
        return
    await session.commit()
    await message.answer(
        "Ты вышел из группы. Выбери снова: /groups",
        reply_markup=main_menu_kb(False, False),
    )


@router.message(Command("my_group"))
async def cmd_my_group(
    message: Message, session, db_user: User | None, is_elder: bool
) -> None:
    sg = await get_study_group(session, db_user)
    if not sg:
        await message.answer("Группа не выбрана. /groups")
        return
    extra = ""
    if is_elder:
        ruz_url = sg.ruz_base_url or config.RUZ_BASE_URL
        extra = (
            f"\nКод приглашения: <code>{sg.join_code}</code>\n"
            f"РУЗ: {ruz_url}\n"
            f"РУЗ (поиск): {ruz_search_for_group(sg)}"
        )
    sem_line = f"\nТекущий учётный семестр: {sg.semester_number}"
    mail_line = ""
    if is_elder and sg.corporate_email:
        mail_line = f"\nКорп. почта (IMAP): <code>{sg.corporate_email}</code>"
    await message.answer(
        f"Группа: {sg.name}{sem_line}{mail_line}{extra}",
        parse_mode="HTML",
    )


@router.message(Command("create_group"))
async def cmd_create_group(message: Message, state: FSMContext, db_user: User | None) -> None:
    fu = message.from_user
    if fu is None or not effective_is_elder(db_user, fu.id, fu.username):
        await message.answer(
            "Создавать группы может только староста. "
            "Нажми /start и выбери «Староста — создать группу» "
            "или попроси владельца бота добавить твой Telegram ID в настройки."
        )
        return
    await state.set_state(CreateGroupStates.name)
    await message.answer(
        "Введите название учебной группы (как она будет отображаться у студентов)."
    )


@router.message(CreateGroupStates.name)
async def create_group_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if len(name) < 2:
        await message.answer("Слишком короткое название, введите ещё раз.")
        return
    await state.update_data(name=name)
    await state.set_state(CreateGroupStates.ruz_source)
    await message.answer(
        "Выберите сайт РУЗ вашего вуза (откуда загружать расписание):",
        reply_markup=_ruz_source_kb(),
    )


@router.message(CreateGroupStates.ruz_source)
async def create_group_ruz_source(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    url = RUZ_SOURCES.get(raw)
    if url:
        await state.update_data(ruz_base_url=url)
        await state.set_state(CreateGroupStates.ruz_search)
        await message.answer(
            "Введите строку для поиска группы в РУЗ (например «ПИ19-5»).\n"
            "Или отправьте «-», чтобы использовать то же название, что и выше.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return
    if raw == "Другой URL":
        await message.answer(
            "Введите полный URL сайта РУЗ (например <code>https://ruz.example.ru</code>).\n"
            "Он должен поддерживать API Галактика РУЗ.",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardRemove(),
        )
        return
    if raw.startswith("http://") or raw.startswith("https://"):
        await state.update_data(ruz_base_url=raw.rstrip("/"))
        await state.set_state(CreateGroupStates.ruz_search)
        await message.answer(
            "Введите строку для поиска группы в РУЗ (например «ПИ19-5»).\n"
            "Или отправьте «-», чтобы использовать то же название, что и выше.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return
    await message.answer(
        "Выберите один из вариантов кнопкой или введите URL вручную:",
        reply_markup=_ruz_source_kb(),
    )


@router.message(CreateGroupStates.ruz_search)
async def create_group_ruz(message: Message, state: FSMContext, db_user: User | None) -> None:
    if not db_user:
        await message.answer("Сначала /start")
        await state.clear()
        return

    data = await state.get_data()
    name = data["name"]
    raw = (message.text or "").strip()
    ruz = name if raw == "-" or not raw else raw

    await state.update_data(ruz_search=ruz)
    await state.set_state(CreateGroupStates.semester)
    await message.answer(
        "Какой сейчас номер семестра у группы? (целое число, например 1, 2 или 3.)"
    )


@router.message(CreateGroupStates.semester)
async def create_group_semester(message: Message, state: FSMContext, db_user: User | None) -> None:
    if not db_user:
        await message.answer("Сначала /start")
        await state.clear()
        return

    raw = (message.text or "").strip()
    try:
        sem = int(raw)
        if sem < 1 or sem > 999:
            raise ValueError
    except ValueError:
        await message.answer("Нужно целое число от 1 до 999. Повтори.")
        return

    await state.update_data(semester=sem)
    await state.set_state(CreateGroupStates.corporate_email)
    skip_kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Пропустить")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await message.answer(
        "Укажите <b>корпоративный e-mail группы</b> (ящик, на который приходят письма для группы).\n"
        "Пример: <code>pi19-5@edu.fa.ru</code>\n\n"
        "Если почты нет — нажмите <b>«Пропустить»</b>. Настроить можно позже.",
        parse_mode="HTML",
        reply_markup=skip_kb,
    )


@router.message(CreateGroupStates.corporate_email)
async def create_group_corporate_email(
    message: Message, state: FSMContext, session, db_user: User | None,
) -> None:
    raw = (message.text or "").strip()
    if raw in ("Пропустить", "-", "нет", "Нет"):
        await _finish_create_group(message, state, session, db_user, corp=None, pwd=None)
        return
    raw = raw.lower()
    if not is_valid_email(raw):
        await message.answer("Похоже на неверный e-mail. Пример: <code>grupa@fa.ru</code>\nИли нажми «Пропустить».", parse_mode="HTML")
        return
    await state.update_data(corporate_email=raw)
    await state.set_state(CreateGroupStates.imap_password)
    await message.answer(
        "Введите <b>пароль приложения</b> для этого ящика (IMAP).\n"
        "Он хранится в базе бота — лучше завести отдельный пароль приложения в настройках почты.\n"
        "Без пароля бот не сможет забирать письма.",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(CreateGroupStates.imap_password)
async def create_group_imap_password(
    message: Message, state: FSMContext, session, db_user: User | None
) -> None:
    if not db_user:
        await message.answer("Сначала /start")
        await state.clear()
        return

    pwd = (message.text or "").strip()
    if len(pwd) < 4:
        await message.answer("Слишком короткий пароль. Введите пароль приложения ещё раз.")
        return

    data = await state.get_data()
    corp = data.get("corporate_email")
    await _finish_create_group(message, state, session, db_user, corp=corp, pwd=pwd)


async def _finish_create_group(
    message: Message,
    state: FSMContext,
    session,
    db_user: User | None,
    corp: str | None,
    pwd: str | None,
) -> None:
    if not db_user:
        await message.answer("Сначала /start")
        await state.clear()
        return

    data = await state.get_data()
    name = data["name"]
    ruz = data["ruz_search"]
    ruz_url = data.get("ruz_base_url") or config.RUZ_BASE_URL
    sem = int(data["semester"])

    code = _gen_join_code()
    for _ in range(20):
        exists = await session.scalar(select(StudyGroup).where(StudyGroup.join_code == code))
        if not exists:
            break
        code = _gen_join_code()

    sg = StudyGroup(
        name=name,
        join_code=code,
        ruz_group_search=ruz,
        ruz_base_url=ruz_url,
        semester_number=sem,
        creator_user_id=db_user.id,
        corporate_email=corp,
        imap_password=pwd,
    )
    session.add(sg)
    # commit, а не flush: освобождаем SQLite write-lock до state.clear() ниже,
    # иначе FSM-сессия зависает в ожидании этого же лока (self-deadlock).
    await session.commit()

    if corp:
        session.add(GroupEmailPollState(study_group_id=sg.id, last_uid=0, bootstrapped=False))

    db_user.study_group_id = sg.id
    db_user.group_name = sg.name

    await state.clear()

    mail_line = f"\nПочта группы: <code>{corp}</code>" if corp else "\nПочта: не привязана (настроить позже: «Почта»)"
    body = (
        f"Группа «{name}» создана (семестр учёта: {sem}).\n"
        f"Код для вступления: <code>{code}</code>\n"
        f"Студенты вводят этот код в боте (кнопка «Ввести код группы» или /join {code})."
        f"{mail_line}"
    )
    msg = await message.answer(
        body,
        parse_mode="HTML",
        reply_markup=main_menu_kb(_is_elder_user(db_user), True),
    )
    try:
        await message.bot.pin_chat_message(
            chat_id=msg.chat.id,
            message_id=msg.message_id,
            disable_notification=True,
        )
    except TelegramBadRequest:
        await message.answer(
            "Не удалось закрепить сообщение с кодом (ограничение Telegram или настройки чата). "
            "Код всегда можно посмотреть: /my_group"
        )


@router.message(JoinCodeStates.code)
async def join_code_entered(message: Message, state: FSMContext, session, db_user: User | None) -> None:
    code = (message.text or "").strip().upper()
    await state.clear()
    await _join_by_code(message, session, db_user, code)


async def _join_by_code(
    message: Message,
    session,
    db_user: User | None,
    code: str,
) -> None:
    if not db_user:
        await message.answer("Сначала /start")
        return
    if not code or len(code) < 4:
        await message.answer("Неверный код.")
        return
    if db_user.study_group_id:
        await message.answer("Ты уже в группе. Смена: /leave_group")
        return
    sg = await session.scalar(
        select(StudyGroup).where(StudyGroup.join_code == code.upper())
    )
    if not sg:
        await message.answer("Группа с таким кодом не найдена.")
        return
    db_user.study_group_id = sg.id
    db_user.group_name = sg.name
    await message.answer(
        f"Ты вступил в группу «{sg.name}».",
        reply_markup=main_menu_kb(_is_elder_user(db_user), True),
    )
