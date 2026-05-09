from aiogram import Dispatcher

from . import (
    attendance,
    birthdays,
    common,
    deadlines,
    grades,
    group_mail,
    group_members,
    groups,
    homework,
    santa,
    schedule,
    site_grades,
)


def register_handlers(dp: Dispatcher) -> None:
    dp.include_router(birthdays.router)
    dp.include_router(common.router)
    dp.include_router(groups.router)
    dp.include_router(group_members.router)
    dp.include_router(group_mail.router)
    dp.include_router(schedule.router)
    dp.include_router(attendance.router)
    dp.include_router(grades.router)
    dp.include_router(site_grades.router)
    dp.include_router(homework.router)
    dp.include_router(deadlines.router)
    dp.include_router(santa.router)
