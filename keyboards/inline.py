from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def payment_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Что входит в стоимость?", callback_data="tariff_details")],
            [InlineKeyboardButton(text="💰 Я оплатил", callback_data="paid")],
        ]
    )


def admin_approve_keyboard(payment_id: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить",
                    callback_data=f"approve_payment:{payment_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=f"reject_payment:{payment_id}",
                ),
            ]
        ]
    )


def admin_panel_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить пробные дни", callback_data="admin_add_trial")],
            [InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users")],
        ]
    )


def auth_method_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📱 Вход по номеру", callback_data="auth_phone")],
            [InlineKeyboardButton(text="🔳 Вход по QR", callback_data="auth_qr")],
        ]
    )


def account_invite_keyboard(invite_id: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Принять",
                    callback_data=f"account_invite_accept:{invite_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=f"account_invite_reject:{invite_id}",
                ),
            ]
        ]
    )


def trial_feedback_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да, понравилось", callback_data="trial_liked_yes"),
                InlineKeyboardButton(text="Нет", callback_data="trial_liked_no"),
            ]
        ]
    )


def start_mailing_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🚀 Запустить рассылку", callback_data="start_mailing_now")]]
    )


def mailing_mode_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📤 Отправить сообщения", callback_data="mail_mode_send")],
            [InlineKeyboardButton(text="📝 Сохранить в черновики", callback_data="mail_mode_draft")],
        ]
    )


def mailing_control_keyboard(control_id: str, paused: bool = False):
    if paused:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="▶️ Продолжить",
                        callback_data=f"mail_continue:{control_id}",
                    )
                ]
            ]
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⏸ Стоп", callback_data=f"mail_stop:{control_id}")]
        ]
    )


def duplicate_usernames_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Да", callback_data="dups_yes")],
            [
                InlineKeyboardButton(
                    text="Нет, изменить список для рассылки",
                    callback_data="dups_edit_list",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Нет, запустить рассылку",
                    callback_data="dups_skip_and_start",
                )
            ],
        ]
    )
