from aiogram.fsm.state import State, StatesGroup


class OnboardingStates(StatesGroup):
    full_name = State()
    birthday = State()


class GradeStates(StatesGroup):
    pick_user = State()
    pick_semester = State()
    pick_subject = State()
    points = State()


class DeadlineStates(StatesGroup):
    title = State()
    description = State()
    when = State()
    subject = State()


class HomeworkStates(StatesGroup):
    pick_date = State()
    pick_schedule = State()
    description = State()
    file = State()


class HomeworkDeleteStates(StatesGroup):
    pick_date = State()
    confirm = State()


class CreateGroupStates(StatesGroup):
    name = State()
    ruz_source = State()
    ruz_search = State()
    semester = State()
    corporate_email = State()
    imap_password = State()


class MailBoxStates(StatesGroup):
    title = State()
    sender_email = State()


class GroupMailSettingsStates(StatesGroup):
    corporate_email = State()
    imap_password = State()


class JoinCodeStates(StatesGroup):
    code = State()


class SchedulePickDateStates(StatesGroup):
    waiting_date = State()


class ProfileBirthdayStates(StatesGroup):
    waiting_birthday = State()


class OrgSetupStates(StatesGroup):
    cookies = State()


class ElderAttendanceStates(StatesGroup):
    waiting_custom_date = State()
