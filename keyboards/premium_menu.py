from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)


def premium_main_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔐 Авторизация", callback_data="login")],
            [InlineKeyboardButton(text="💳 Купить доступ", callback_data="subscribe")],
            [InlineKeyboardButton(text="🚀 Создать рассылку", callback_data="mailing")],
            [InlineKeyboardButton(text="👤 Профиль", callback_data="profile")],
        ]
    )


def premium_reply_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔐 Авторизация")],
            [KeyboardButton(text="💳 Купить доступ")],
            [KeyboardButton(text="👤 Профиль")],
            [KeyboardButton(text="🚀 Создать рассылку")],
            [KeyboardButton(text="🔎 Сканирование чатов")],
            [KeyboardButton(text="🧪 Проверка каналов")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие",
    )
