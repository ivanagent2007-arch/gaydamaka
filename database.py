from __future__ import annotations

import json
from datetime import date, datetime
from enum import Enum
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

import config
from utils.secrets_store import decrypt_secret, encrypt_secret


class UserRole(str, Enum):
    student = "student"
    elder = "elder"
    deputy_elder = "deputy_elder"


class Base(DeclarativeBase):
    pass


class _EncryptedText(TypeDecorator):
    """Прозрачное шифрование: на запись — encrypt_secret, на чтение — decrypt_secret.
    Подкладочный тип — Text (ciphertext может быть длиннее исходного значения).
    Старые plaintext-значения читаются без ошибок (см. decrypt_secret)."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return encrypt_secret(value) if value else value

    def process_result_value(self, value, dialect):
        return decrypt_secret(value) if value else value


class StudyGroup(Base):
    __tablename__ = "study_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200))
    join_code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    ruz_group_search: Mapped[str | None] = mapped_column(String(200), nullable=True)
    ruz_base_url: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # Текущий учётный семестр (последний); при появлении новых предметов в РУЗ увеличивается.
    semester_number: Mapped[int] = mapped_column(Integer, default=1)
    creator_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Корпоративный ящик группы (логин IMAP = этот адрес)
    corporate_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Пароль приложения / IMAP — шифруется на записи, расшифровывается на чтении.
    imap_password: Mapped[str | None] = mapped_column(_EncryptedText, nullable=True)
    # org.fa.ru — cookies браузерной сессии — тоже шифруются.
    org_cookies: Mapped[str | None] = mapped_column(_EncryptedText, nullable=True)

    creator: Mapped["User | None"] = relationship(
        foreign_keys=[creator_user_id],
        back_populates="created_groups",
    )
    members: Mapped[list["User"]] = relationship(
        back_populates="study_group",
        foreign_keys="User.study_group_id",
    )
    email_mailboxes: Mapped[list["GroupEmailMailbox"]] = relationship(
        back_populates="study_group",
        cascade="all, delete-orphan",
    )
    email_messages: Mapped[list["GroupEmailMessage"]] = relationship(
        back_populates="study_group",
        cascade="all, delete-orphan",
    )


class GroupEmailMailbox(Base):
    """Ящик: письма от указанного отправителя попадают сюда (для сортировки в боте)."""

    __tablename__ = "group_email_mailboxes"
    __table_args__ = (
        UniqueConstraint("study_group_id", "sender_email", name="uq_mailbox_group_sender"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    study_group_id: Mapped[int] = mapped_column(
        ForeignKey("study_groups.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    sender_email: Mapped[str] = mapped_column(String(255), index=True)

    study_group: Mapped["StudyGroup"] = relationship(back_populates="email_mailboxes")
    messages: Mapped[list["GroupEmailMessage"]] = relationship(
        back_populates="mailbox",
        cascade="all, delete-orphan",
    )


class GroupEmailMessage(Base):
    """Копия письма для просмотра в боте и дедупликации уведомлений."""

    __tablename__ = "group_email_messages"
    __table_args__ = (
        UniqueConstraint("study_group_id", "message_id_header", name="uq_email_msg_group_mid"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    study_group_id: Mapped[int] = mapped_column(
        ForeignKey("study_groups.id", ondelete="CASCADE"), index=True
    )
    mailbox_id: Mapped[int | None] = mapped_column(
        ForeignKey("group_email_mailboxes.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    message_id_header: Mapped[str] = mapped_column(String(512), default="")
    sender: Mapped[str] = mapped_column(String(512), default="")
    subject: Mapped[str] = mapped_column(String(1024), default="")
    body_preview: Mapped[str] = mapped_column(Text, default="")
    received_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    study_group: Mapped["StudyGroup"] = relationship(back_populates="email_messages")
    mailbox: Mapped["GroupEmailMailbox | None"] = relationship(back_populates="messages")
    attachments: Mapped[list["GroupEmailAttachment"]] = relationship(
        back_populates="message",
        cascade="all, delete-orphan",
    )


class GroupEmailAttachment(Base):
    """Файл вложения, сохранённый с письма (для мини-приложения)."""

    __tablename__ = "group_email_attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(
        ForeignKey("group_email_messages.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(255), default="")
    stored_path: Mapped[str] = mapped_column(String(512), default="")
    mime_type: Mapped[str] = mapped_column(String(128), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)

    message: Mapped["GroupEmailMessage"] = relationship(back_populates="attachments")


class GroupEmailPollState(Base):
    """Последний обработанный UID в INBOX (не дублировать письма)."""

    __tablename__ = "group_email_poll_state"

    study_group_id: Mapped[int] = mapped_column(
        ForeignKey("study_groups.id", ondelete="CASCADE"), primary_key=True
    )
    last_uid: Mapped[int] = mapped_column(Integer, default=0)
    # Первая синхронизация: пустой ящик vs «пропустить старые письма»
    bootstrapped: Mapped[bool] = mapped_column(Boolean, default=False)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Telegram-ID давно вышел за INT4 (на Postgres INTEGER = INT4, переполнение для новых юзеров).
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255), default="")
    group_name: Mapped[str] = mapped_column(String(128), default="")
    study_group_id: Mapped[int | None] = mapped_column(
        ForeignKey("study_groups.id", ondelete="SET NULL"),
        nullable=True,
    )
    role: Mapped[UserRole] = mapped_column(SAEnum(UserRole), default=UserRole.student)
    birthday_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    birthday_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    birth_year: Mapped[int | None] = mapped_column(Integer, nullable=True)

    study_group: Mapped["StudyGroup | None"] = relationship(
        back_populates="members",
        foreign_keys=[study_group_id],
    )
    created_groups: Mapped[list["StudyGroup"]] = relationship(
        back_populates="creator",
        foreign_keys="StudyGroup.creator_user_id",
    )
    grades: Mapped[list["Grade"]] = relationship(back_populates="user")
    attendance: Mapped[list["Attendance"]] = relationship(back_populates="user")


class Schedule(Base):
    __tablename__ = "schedules"
    __table_args__ = (
        UniqueConstraint(
            "study_group_id",
            "lesson_date",
            "start_time",
            "subject",
            "teacher",
            "lesson_kind",
            "contingent_label",
            name="uq_schedule_group_slot_teacher_kind_contingent",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    study_group_id: Mapped[int | None] = mapped_column(
        ForeignKey("study_groups.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    group_name: Mapped[str] = mapped_column(String(128), index=True, default="")
    lesson_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    day_of_week: Mapped[int] = mapped_column(Integer, default=0)
    lesson_number: Mapped[int] = mapped_column(Integer, default=1)
    subject: Mapped[str] = mapped_column(String(512))
    teacher: Mapped[str] = mapped_column(String(512), default="")
    room: Mapped[str] = mapped_column(String(128), default="")
    start_time: Mapped[str] = mapped_column(String(16))
    end_time: Mapped[str] = mapped_column(String(16))
    # лекция / семинар / практика / лаб. — из РУЗ (kindOfWork и др.)
    lesson_kind: Mapped[str] = mapped_column(String(64), default="")
    # подгруппа / поток / родительское расписание — из РУЗ (subGroup, stream, listSubGroups…)
    contingent_label: Mapped[str] = mapped_column(String(255), default="")

    homework: Mapped[list["Homework"]] = relationship(back_populates="schedule")
    attendance: Mapped[list["Attendance"]] = relationship(back_populates="schedule")


class GroupSemesterSubject(Base):
    """Предметы группы по семестрам (заполняется из расписания; новые семестры — при появлении новых предметов)."""

    __tablename__ = "group_semester_subjects"
    __table_args__ = (
        UniqueConstraint(
            "study_group_id",
            "semester_number",
            "subject_key",
            name="uq_gss_group_sem_subj",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    study_group_id: Mapped[int] = mapped_column(
        ForeignKey("study_groups.id", ondelete="CASCADE"), index=True
    )
    semester_number: Mapped[int] = mapped_column(Integer, index=True)
    subject: Mapped[str] = mapped_column(String(512))
    subject_key: Mapped[str] = mapped_column(String(512), index=True)


class Grade(Base):
    __tablename__ = "grades"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "semester_number",
            "subject_key",
            name="uq_grade_user_sem_subj",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    semester_number: Mapped[int] = mapped_column(Integer, default=1, index=True)
    subject: Mapped[str] = mapped_column(String(512))
    subject_key: Mapped[str] = mapped_column(String(512))
    points: Mapped[float] = mapped_column(Float, default=0.0)

    user: Mapped["User"] = relationship(back_populates="grades")


class SiteGrade(Base):
    """Баллы с org.fa.ru, загруженные через аккаунт старосты."""

    __tablename__ = "site_grades"
    __table_args__ = (
        UniqueConstraint(
            "study_group_id",
            "org_student_id",
            "discipline",
            name="uq_site_grade_group_student_disc",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    study_group_id: Mapped[int] = mapped_column(
        ForeignKey("study_groups.id", ondelete="CASCADE"), index=True
    )
    org_student_id: Mapped[int] = mapped_column(Integer)
    student_name: Mapped[str] = mapped_column(String(255), default="")
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    discipline: Mapped[str] = mapped_column(String(512))
    attendance_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    marks_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Homework(Base):
    __tablename__ = "homeworks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    schedule_id: Mapped[int | None] = mapped_column(
        ForeignKey("schedules.id", ondelete="SET NULL"), nullable=True
    )
    description: Mapped[str] = mapped_column(Text, default="")
    file_paths: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    schedule: Mapped["Schedule | None"] = relationship(back_populates="homework")

    def file_list(self) -> list[str]:
        try:
            return json.loads(self.file_paths) if self.file_paths else []
        except json.JSONDecodeError:
            return []


class BirthdayReminderSent(Base):
    """Отметка, что напоминание о ДР отправляли (чтобы не дублировать)."""

    __tablename__ = "birthday_reminders_sent"
    __table_args__ = (
        UniqueConstraint(
            "celebrant_user_id",
            "event_year",
            "kind",
            name="uq_birthday_reminder",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    celebrant_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    event_year: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(8))


class Attendance(Base):
    __tablename__ = "attendance"
    __table_args__ = (
        UniqueConstraint("user_id", "schedule_id", "mark_date", name="uq_attendance_day"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    schedule_id: Mapped[int] = mapped_column(ForeignKey("schedules.id", ondelete="CASCADE"))
    mark_date: Mapped[date] = mapped_column(Date)
    is_present: Mapped[bool] = mapped_column(Boolean, default=True)

    user: Mapped["User"] = relationship(back_populates="attendance")
    schedule: Mapped["Schedule"] = relationship(back_populates="attendance")


class Deadline(Base):
    __tablename__ = "deadlines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    study_group_id: Mapped[int | None] = mapped_column(
        ForeignKey("study_groups.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(512))
    description: Mapped[str] = mapped_column(Text, default="")
    deadline_date: Mapped[datetime] = mapped_column(DateTime)
    subject: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    notified_24h: Mapped[bool] = mapped_column(Boolean, default=False)
    homework_id: Mapped[int | None] = mapped_column(
        ForeignKey("homeworks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )


class SantaGame(Base):
    __tablename__ = "santa_games"
    __table_args__ = (
        UniqueConstraint("study_group_id", "year", name="uq_santa_game_group_year"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    study_group_id: Mapped[int | None] = mapped_column(
        ForeignKey("study_groups.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    group_name: Mapped[str] = mapped_column(String(128), default="")
    year: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    pairs: Mapped[list["SantaPair"]] = relationship(back_populates="game")


class SantaPair(Base):
    __tablename__ = "santa_pairs"
    __table_args__ = (
        UniqueConstraint("game_id", "giver_id", name="uq_santa_giver"),
        UniqueConstraint("game_id", "receiver_id", name="uq_santa_receiver"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("santa_games.id", ondelete="CASCADE"))
    giver_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    receiver_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    game: Mapped["SantaGame"] = relationship(back_populates="pairs")


class FSMStateRow(Base):
    """Персистентное FSM-состояние aiogram (вместо MemoryStorage): диалоги
    переживают рестарт бота — староста не теряет шаг при настройке группы, ввод
    дедлайна не сбрасывается и т.п."""

    __tablename__ = "aiogram_fsm"

    bot_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    thread_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=0)
    business_connection_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=""
    )
    state: Mapped[str | None] = mapped_column(Text, nullable=True)
    data_json: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


_engine_kwargs: dict = {"echo": False}
if config.DATABASE_URL.startswith("sqlite"):
    # SQLite по умолчанию ждёт лока всего 5 секунд (потом OperationalError: database is locked).
    # Бот делает несколько одновременных запросов на каждый /start (DbSessionMiddleware +
    # отдельная FSM-сессия). На 30 секунд хватит даже долгих миграций.
    _engine_kwargs["connect_args"] = {"timeout": 30}

engine = create_async_engine(config.DATABASE_URL, **_engine_kwargs)
async_session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


if config.DATABASE_URL.startswith("sqlite"):
    # WAL-режим: позволяет одному подключению писать, пока другие читают — без этого
    # выдача FSM-состояния параллельно с записью в users почти всегда даёт
    # «database is locked». synchronous=NORMAL ускоряет коммиты при WAL.
    from sqlalchemy import event as _sa_event

    @_sa_event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()


def _sqlite_columns(sync_conn, table: str) -> set[str]:
    rows = sync_conn.execute(text(f'PRAGMA table_info("{table}")')).fetchall()
    return {r[1] for r in rows}


def _migrate_sqlite_schema(sync_conn) -> None:
    cols = _sqlite_columns(sync_conn, "users")
    if "study_group_id" not in cols:
        sync_conn.execute(
            text("ALTER TABLE users ADD COLUMN study_group_id INTEGER REFERENCES study_groups(id)")
        )
    sched_cols = _sqlite_columns(sync_conn, "schedules")
    if "study_group_id" not in sched_cols:
        sync_conn.execute(
            text(
                "ALTER TABLE schedules ADD COLUMN study_group_id INTEGER REFERENCES study_groups(id)"
            )
        )
    dl_cols = _sqlite_columns(sync_conn, "deadlines")
    if "study_group_id" not in dl_cols:
        sync_conn.execute(
            text(
                "ALTER TABLE deadlines ADD COLUMN study_group_id INTEGER REFERENCES study_groups(id)"
            )
        )
    sg_cols = _sqlite_columns(sync_conn, "santa_games")
    if "study_group_id" not in sg_cols:
        sync_conn.execute(
            text(
                "ALTER TABLE santa_games ADD COLUMN study_group_id INTEGER REFERENCES study_groups(id)"
            )
        )


def _schedules_table_needs_rebuild(sync_conn) -> bool:
    row = sync_conn.execute(
        text("SELECT sql FROM sqlite_master WHERE type='table' AND name='schedules'")
    ).fetchone()
    if not row or not row[0]:
        return False
    sql_lower = row[0].lower()
    if "uq_schedule_group_slot_teacher_kind_contingent" in sql_lower:
        return False
    if "uq_schedule_group_slot_teacher_kind" in sql_lower:
        return False
    if "uq_schedule_group_slot_teacher" in sql_lower:
        return False
    # старые схемы без преподавателя в уникальном ключе
    return True


def _schedules_has_lesson_kind_constraint(sync_conn) -> bool:
    row = sync_conn.execute(
        text("SELECT sql FROM sqlite_master WHERE type='table' AND name='schedules'")
    ).fetchone()
    if not row or not row[0]:
        return False
    return "uq_schedule_group_slot_teacher_kind" in (row[0] or "")


def _migrate_sqlite_schedules_rebuild(sync_conn) -> None:
    if not _schedules_table_needs_rebuild(sync_conn):
        return
    sync_conn.execute(text("PRAGMA foreign_keys=OFF"))
    try:
        sync_conn.execute(text("DELETE FROM attendance"))
        sync_conn.execute(text("UPDATE homeworks SET schedule_id = NULL"))
        sync_conn.execute(text("DROP TABLE schedules"))
        Schedule.__table__.create(sync_conn, checkfirst=True)
    finally:
        sync_conn.execute(text("PRAGMA foreign_keys=ON"))


def _migrate_sqlite_study_groups_semester(sync_conn) -> None:
    cols = _sqlite_columns(sync_conn, "study_groups")
    if "semester_number" not in cols:
        sync_conn.execute(
            text("ALTER TABLE study_groups ADD COLUMN semester_number INTEGER DEFAULT 1 NOT NULL")
        )


def _migrate_sqlite_grades_semester(sync_conn) -> None:
    cols = _sqlite_columns(sync_conn, "grades")
    if "semester_number" in cols and "subject_key" in cols:
        return
    sync_conn.execute(text("PRAGMA foreign_keys=OFF"))
    try:
        sync_conn.execute(
            text(
                """
                CREATE TABLE grades_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    semester_number INTEGER NOT NULL DEFAULT 1,
                    subject VARCHAR(512) NOT NULL,
                    subject_key VARCHAR(512) NOT NULL,
                    points FLOAT NOT NULL DEFAULT 0.0,
                    UNIQUE (user_id, semester_number, subject_key)
                )
                """
            )
        )
        sync_conn.execute(
            text(
                """
                INSERT INTO grades_new (id, user_id, semester_number, subject, subject_key, points)
                SELECT id, user_id, 1, subject, lower(trim(subject)), points FROM grades
                """
            )
        )
        sync_conn.execute(text("DROP TABLE grades"))
        sync_conn.execute(text("ALTER TABLE grades_new RENAME TO grades"))
    finally:
        sync_conn.execute(text("PRAGMA foreign_keys=ON"))


def _migrate_sqlite_user_birthday(sync_conn) -> None:
    cols = _sqlite_columns(sync_conn, "users")
    if "birthday_month" not in cols:
        sync_conn.execute(text("ALTER TABLE users ADD COLUMN birthday_month INTEGER"))
    if "birthday_day" not in cols:
        sync_conn.execute(text("ALTER TABLE users ADD COLUMN birthday_day INTEGER"))
    if "birth_year" not in cols:
        sync_conn.execute(text("ALTER TABLE users ADD COLUMN birth_year INTEGER"))


def _migrate_sqlite_study_group_mail(sync_conn) -> None:
    cols = _sqlite_columns(sync_conn, "study_groups")
    if "corporate_email" not in cols:
        sync_conn.execute(text("ALTER TABLE study_groups ADD COLUMN corporate_email VARCHAR(255)"))
    if "imap_password" not in cols:
        sync_conn.execute(text("ALTER TABLE study_groups ADD COLUMN imap_password VARCHAR(255)"))


def _recover_schedules_after_failed_lesson_kind_migration(sync_conn) -> None:
    """Если миграция lesson_kind упала после RENAME: есть schedules_old и пустой schedules."""
    tables = {
        r[0]
        for r in sync_conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table'")
        ).fetchall()
    }
    if "schedules_old" not in tables:
        return
    if "schedules" in tables:
        n = sync_conn.execute(text("SELECT COUNT(1) FROM schedules")).scalar() or 0
        if n > 0:
            return
        sync_conn.execute(text("DROP TABLE schedules"))
    sync_conn.execute(text("ALTER TABLE schedules_old RENAME TO schedules"))


def _migrate_sqlite_schedules_lesson_kind(sync_conn) -> None:
    """Добавить lesson_kind и UNIQUE с типом занятия (лекция/семинар и т.д.)."""
    if _schedules_has_lesson_kind_constraint(sync_conn):
        return
    sync_conn.execute(text("PRAGMA foreign_keys=OFF"))
    try:
        sync_conn.execute(text("DELETE FROM attendance"))
        sync_conn.execute(text("UPDATE homeworks SET schedule_id = NULL"))
        sync_conn.execute(text("ALTER TABLE schedules RENAME TO schedules_old"))
        old_cols = _sqlite_columns(sync_conn, "schedules_old")
        lk_expr = "COALESCE(lesson_kind, '')" if "lesson_kind" in old_cols else "''"
        # Имена индексов в SQLite глобальны; после RENAME они остаются у schedules_old.
        for ix in ("ix_schedules_group_name", "ix_schedules_study_group_id"):
            sync_conn.execute(text(f"DROP INDEX IF EXISTS {ix}"))
        Schedule.__table__.create(sync_conn, checkfirst=True)
        sync_conn.execute(
            text(
                f"""
                INSERT INTO schedules (
                    id, study_group_id, group_name, lesson_date, day_of_week,
                    lesson_number, subject, teacher, room, start_time, end_time, lesson_kind,
                    contingent_label
                )
                SELECT
                    id, study_group_id, group_name, lesson_date, day_of_week,
                    lesson_number, subject, teacher, room, start_time, end_time, {lk_expr},
                    ''
                FROM schedules_old
                """
            )
        )
        sync_conn.execute(text("DROP TABLE schedules_old"))
    finally:
        sync_conn.execute(text("PRAGMA foreign_keys=ON"))


def _migrate_sqlite_poll_bootstrapped(sync_conn) -> None:
    cols = _sqlite_columns(sync_conn, "group_email_poll_state")
    if "bootstrapped" not in cols:
        sync_conn.execute(
            text("ALTER TABLE group_email_poll_state ADD COLUMN bootstrapped INTEGER NOT NULL DEFAULT 0")
        )
    sync_conn.execute(
        text("UPDATE group_email_poll_state SET bootstrapped = 1 WHERE last_uid > 0")
    )


def _migrate_sqlite_study_group_ruz_base_url(sync_conn) -> None:
    cols = _sqlite_columns(sync_conn, "study_groups")
    if "ruz_base_url" not in cols:
        sync_conn.execute(text("ALTER TABLE study_groups ADD COLUMN ruz_base_url VARCHAR(300)"))


def _migrate_sqlite_study_group_org_cookies(sync_conn) -> None:
    cols = _sqlite_columns(sync_conn, "study_groups")
    if "org_cookies" not in cols:
        sync_conn.execute(text("ALTER TABLE study_groups ADD COLUMN org_cookies TEXT"))


def _migrate_sqlite_deadline_homework_id(sync_conn) -> None:
    cols = _sqlite_columns(sync_conn, "deadlines")
    if "homework_id" in cols:
        return
    sync_conn.execute(
        text(
            "ALTER TABLE deadlines ADD COLUMN homework_id INTEGER "
            "REFERENCES homeworks(id)"
        )
    )


def _migrate_sqlite_schedules_contingent_label(sync_conn) -> None:
    """Подгруппа/поток из РУЗ + расширенный UNIQUE (включая contingent_label)."""
    cols = _sqlite_columns(sync_conn, "schedules")
    if "contingent_label" in cols:
        return
    sync_conn.execute(text("PRAGMA foreign_keys=OFF"))
    try:
        sync_conn.execute(text("DELETE FROM attendance"))
        sync_conn.execute(text("UPDATE homeworks SET schedule_id = NULL"))
        sync_conn.execute(text("ALTER TABLE schedules RENAME TO schedules_old"))
        old_cols = _sqlite_columns(sync_conn, "schedules_old")
        lk_expr = "COALESCE(lesson_kind, '')" if "lesson_kind" in old_cols else "''"
        for ix in ("ix_schedules_group_name", "ix_schedules_study_group_id"):
            sync_conn.execute(text(f"DROP INDEX IF EXISTS {ix}"))
        Schedule.__table__.create(sync_conn, checkfirst=True)
        sync_conn.execute(
            text(
                f"""
                INSERT INTO schedules (
                    id, study_group_id, group_name, lesson_date, day_of_week,
                    lesson_number, subject, teacher, room, start_time, end_time, lesson_kind,
                    contingent_label
                )
                SELECT
                    id, study_group_id, group_name, lesson_date, day_of_week,
                    lesson_number, subject, teacher, room, start_time, end_time, {lk_expr},
                    ''
                FROM schedules_old
                """
            )
        )
        sync_conn.execute(text("DROP TABLE schedules_old"))
    finally:
        sync_conn.execute(text("PRAGMA foreign_keys=ON"))


def _migrate_sqlite_site_grades_marks_json(sync_conn) -> None:
    tables = {
        r[0]
        for r in sync_conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table'")
        ).fetchall()
    }
    if "site_grades" not in tables:
        return
    cols = _sqlite_columns(sync_conn, "site_grades")
    if "marks_json" not in cols:
        sync_conn.execute(text("ALTER TABLE site_grades ADD COLUMN marks_json TEXT"))


async def init_db() -> None:
    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    (config.BASE_DIR / "data").mkdir(parents=True, exist_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        dialect = conn.engine.dialect.name
        if dialect == "sqlite":
            await conn.run_sync(_migrate_sqlite_schema)
            await conn.run_sync(_migrate_sqlite_schedules_rebuild)
            await conn.run_sync(_migrate_sqlite_study_groups_semester)
            await conn.run_sync(_migrate_sqlite_grades_semester)
            await conn.run_sync(_migrate_sqlite_user_birthday)
            await conn.run_sync(_migrate_sqlite_study_group_mail)
            await conn.run_sync(_migrate_sqlite_poll_bootstrapped)
            await conn.run_sync(_recover_schedules_after_failed_lesson_kind_migration)
            await conn.run_sync(_migrate_sqlite_schedules_lesson_kind)
            await conn.run_sync(_migrate_sqlite_schedules_contingent_label)
            await conn.run_sync(_migrate_sqlite_study_group_ruz_base_url)
            await conn.run_sync(_migrate_sqlite_study_group_org_cookies)
            await conn.run_sync(_migrate_sqlite_site_grades_marks_json)
            await conn.run_sync(_migrate_sqlite_deadline_homework_id)
        elif dialect in ("postgresql", "postgres"):
            await conn.run_sync(_migrate_postgres_bigint_telegram_ids)
    await _encrypt_legacy_secrets_at_rest()


def _migrate_postgres_bigint_telegram_ids(sync_conn) -> None:
    """Расширяет колонки Telegram-ID до BIGINT на Postgres, если они ещё INT4.

    Telegram-ID давно превышает 2^31, а изначально модель использовала Integer
    (= INT4 на Postgres), из-за чего INSERT новых пользователей падал с
    переполнением. Безопасно при повторных запусках: пропускает уже BIGINT.

    Реализация через ``information_schema``: сначала смотрим текущий тип,
    ALTER выполняем только когда действительно надо. Это критично, потому что
    в Postgres любая упавшая команда в транзакции переводит её в ``aborted``,
    и все последующие statements тихо игнорируются. Идемпотентная проверка
    позволяет обойтись без savepoints и не зависит от порядка ALTER.
    """
    import logging as _logging

    log = _logging.getLogger(__name__)

    targets = [
        ("users", "telegram_id"),
        ("site_grades", "telegram_id"),
        ("aiogram_fsm", "bot_id"),
        ("aiogram_fsm", "chat_id"),
        ("aiogram_fsm", "user_id"),
        ("aiogram_fsm", "thread_id"),
    ]

    for table, column in targets:
        row = sync_conn.execute(
            text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_schema = current_schema() "
                "AND table_name = :t AND column_name = :c"
            ),
            {"t": table, "c": column},
        ).fetchone()
        if row is None:
            # Колонки нет (или таблицы) — пропускаем; create_all создаст её с BIGINT по модели.
            continue
        current_type = (row[0] or "").lower()
        if current_type == "bigint":
            continue
        try:
            sync_conn.execute(
                text(f'ALTER TABLE "{table}" ALTER COLUMN "{column}" TYPE BIGINT')
            )
            log.info("[bigint_migration] %s.%s: %s -> bigint", table, column, current_type)
        except Exception as ex:  # noqa: BLE001
            log.warning(
                "[bigint_migration] %s.%s: ALTER FAILED (%s): %s",
                table, column, ex.__class__.__name__, ex,
            )
            raise  # пускай init_db упадёт громко, иначе симптомы те же: /start не отвечает


async def _encrypt_legacy_secrets_at_rest() -> None:
    """Одноразовая миграция: шифрует имеющиеся plaintext-значения IMAP-паролей
    и cookies org.fa.ru. Безопасно при повторных запусках — уже зашифрованные строки
    (с префиксом ``enc:v1:``) пропускаются."""
    from sqlalchemy.orm.attributes import flag_modified

    async with async_session_maker() as session:
        raw_rows = (
            await session.execute(
                text(
                    "SELECT id, imap_password, org_cookies FROM study_groups "
                    "WHERE (imap_password IS NOT NULL AND imap_password != '' "
                    "AND imap_password NOT LIKE 'enc:v1:%') "
                    "OR (org_cookies IS NOT NULL AND org_cookies != '' "
                    "AND org_cookies NOT LIKE 'enc:v1:%')"
                )
            )
        ).all()
        if not raw_rows:
            return
        for row in raw_rows:
            sg = await session.get(StudyGroup, row.id)
            if sg is None:
                continue
            # Принудительно метим поля грязными — значения после ORM-чтения уже plaintext
            # (decrypt пропустил их без префикса), и SQLAlchemy сам бы не увидел изменения.
            if sg.imap_password:
                flag_modified(sg, "imap_password")
            if sg.org_cookies:
                flag_modified(sg, "org_cookies")
        await session.commit()
