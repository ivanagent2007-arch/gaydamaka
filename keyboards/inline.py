from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def onboarding_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Я староста — создать группу",
                    callback_data="onboard:elder",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Я студент — у меня есть код",
                    callback_data="onboard:student",
                )
            ],
        ]
    )
