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
            [InlineKeyboardButton(text="Что входит в стоимость?", callback_data="tariff_details")],
            [InlineKeyboardButton(text="🚀 Создать рассылку", callback_data="mailing")],
            [InlineKeyboardButton(text="➕ Добавить аккаунт", callback_data="add_account")],
            [InlineKeyboardButton(text="👤 Профиль", callback_data="profile")],
        ]
    )


def premium_reply_menu(has_access: bool = False):
    keyboard = [
        [KeyboardButton(text="🔐 Авторизация")],
        [KeyboardButton(text="💳 Купить доступ")],
        [KeyboardButton(text="Что входит в стоимость?")],
        [KeyboardButton(text="👤 Профиль")],
        [KeyboardButton(text="🚀 Создать рассылку")],
    ]
    if has_access:
        keyboard.append([KeyboardButton(text="➕ Добавить аккаунт")])
    keyboard.extend(
        [
            [KeyboardButton(text="🔎 Сканирование чатов")],
            [KeyboardButton(text="🧪 Проверка каналов")],
        ]
    )
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        input_field_placeholder="Выберите действие",
    )
