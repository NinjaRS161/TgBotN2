from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import SUB_PRICE, TRIAL_DAYS
from database import (
    check_subscription,
    get_access_status,
    get_session,
    is_trial_feedback_pending,
    save_user_profile,
)
from handlers import mailing  # чтобы запускать FSM рассылки
from handlers.trial import send_trial_feedback_request
from keyboards.inline import auth_method_keyboard, payment_keyboard
from keyboards.premium_menu import premium_reply_menu
from utils.animations import typing_animation

router = Router()


TARIFF_DETAILS_TEXT = (
    "💎 <b>Что входит в стоимость</b>\n\n"
    f"• {TRIAL_DAYS} пробных дня для знакомства с ботом.\n"
    "• Разовый доступ к боту без срока окончания.\n"
    "• Авторизация Telegram-аккаунта по QR или номеру.\n"
    "• Массовая рассылка по username с настраиваемой задержкой.\n"
    "• Режим сохранения сообщений в черновики.\n"
    "• Защита от повторной рассылки по уже использованным username.\n"
    "• Live-статистика выполнения и управление рассылкой: стоп/продолжить.\n"
    "• Сканирование чатов и комментариев для сбора username.\n"
    "• Проверка, у каких username есть Telegram-каналы.\n"
    "• Возможность добавить второй аккаунт через подтверждение приглашения."
)


async def _send_login_prompt(bot: Bot, user_id: int):
    await bot.send_message(
        chat_id=user_id,
        text="🔐 Выберите способ авторизации:",
        reply_markup=auth_method_keyboard(),
    )


async def _send_buy_access_prompt(bot: Bot, user_id: int):
    await bot.send_message(
        chat_id=user_id,
        text=(
            f"💳 Доступ к боту стоит {SUB_PRICE} ₽.\n\n"
            f"Это разовая покупка без срока окончания.\n"
            f"Переведите сумму на мои реквизиты: 89508543308(Сбербанк) и нажмите кнопку ниже:"
        ),
        reply_markup=payment_keyboard(),
    )


async def _send_profile(bot: Bot, user_id: int):
    access_status = await get_access_status(user_id)
    is_active = access_status["status"] in {"paid", "trial"}
    has_session = bool(await get_session(user_id))
    if access_status["status"] == "paid":
        status_text = "✅ Полный доступ к боту активен"
    elif access_status["status"] == "trial":
        trial_until = access_status.get("trial_until")
        until_text = trial_until.strftime("%d.%m.%Y %H:%M") if trial_until else "скоро"
        status_text = f"✅ Пробный доступ активен до {until_text}"
    elif access_status["status"] == "expired":
        status_text = "⌛ Пробный доступ истёк"
    else:
        status_text = "❌ Доступ к боту не активирован"
    auth_text = "✅ Авторизация сохранена" if has_session else "❌ Авторизация не пройдена"

    await bot.send_message(
        chat_id=user_id,
        text=f"👤 <b>Профиль</b>\n\n{status_text}\n{auth_text}",
        parse_mode="HTML",
    )


async def _send_tariff_details(bot: Bot, user_id: int):
    await bot.send_message(
        chat_id=user_id,
        text=TARIFF_DETAILS_TEXT,
        parse_mode="HTML",
    )


async def _start_mailing(bot: Bot, user_id: int, state: FSMContext):
    if not await check_subscription(user_id):
        await bot.send_message(user_id, "❌ У вас нет доступа к боту.")
        return

    await state.set_state(mailing.MailingStates.text)
    await bot.send_message(
        user_id,
        "🚀 Давайте создадим рассылку.\n"
        "Введите текст для рассылки. Можно с форматированием: жирный, курсив, зачеркнутый, подчеркнутый и т.д.\n"
        "Для отмены в любой момент отправьте /cancel.",
    )


@router.message(Command("start"))
async def start_handler(message: Message):
    await save_user_profile(message.from_user.id, message.from_user.username)
    await typing_animation(message.bot, message.chat.id, 2)
    has_access = await check_subscription(message.from_user.id)

    if await is_trial_feedback_pending(message.from_user.id):
        await send_trial_feedback_request(message.bot, message.from_user.id)

    await message.bot.send_message(
        chat_id=message.chat.id,
        text=(
            "💎 <b>Premium Mailing System</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "🚀 Автоматические рассылки\n"
            "🔐 Безопасная авторизация\n"
            "📊 Live-статистика\n\n"
            "👇 Выберите действие:"
        ),
        parse_mode="HTML",
        reply_markup=premium_reply_menu(has_access=has_access),
    )


@router.callback_query(F.data == "login")
async def login_callback(callback: CallbackQuery):
    await callback.answer()
    await _send_login_prompt(callback.bot, callback.from_user.id)


@router.callback_query(F.data == "subscribe")
async def subscribe_callback(callback: CallbackQuery):
    await callback.answer()
    if not await get_session(callback.from_user.id):
        await callback.bot.send_message(
            callback.from_user.id,
            "❌ Сначала зарегистрируйтесь через кнопку 'Авторизация'.",
        )
        return
    await _send_buy_access_prompt(callback.bot, callback.from_user.id)


@router.callback_query(F.data == "tariff_details")
async def tariff_details_callback(callback: CallbackQuery):
    await callback.answer()
    await _send_tariff_details(callback.bot, callback.from_user.id)


@router.callback_query(F.data == "profile")
async def profile_callback(callback: CallbackQuery):
    await callback.answer()
    await _send_profile(callback.bot, callback.from_user.id)


@router.callback_query(F.data == "mailing")
async def mailing_callback(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await _start_mailing(callback.bot, callback.from_user.id, state)


@router.message(F.text.in_(["Авторизация", "🔐 Авторизация"]))
async def login_button(message: Message):
    await _send_login_prompt(message.bot, message.from_user.id)


@router.message(F.text.in_(["Купить доступ", "💳 Купить доступ", "Подписка", "💳 Подписка"]))
async def subscribe_button(message: Message):
    if not await get_session(message.from_user.id):
        await message.answer("❌ Сначала зарегистрируйтесь через кнопку 'Авторизация'.")
        return
    await _send_buy_access_prompt(message.bot, message.from_user.id)


@router.message(F.text.in_(["Что входит в стоимость", "Что входит в стоимость?"]))
async def tariff_details_button(message: Message):
    await _send_tariff_details(message.bot, message.from_user.id)


@router.message(F.text.in_(["Профиль", "👤 Профиль"]))
async def profile_button(message: Message):
    await _send_profile(message.bot, message.from_user.id)


@router.message(F.text.in_(["Создать рассылку", "🚀 Создать рассылку"]))
async def mailing_button(message: Message, state: FSMContext):
    await _start_mailing(message.bot, message.from_user.id, state)
