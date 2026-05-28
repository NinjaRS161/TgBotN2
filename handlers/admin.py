from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from config import ADMIN_ID
from database import add_trial_days, get_user_by_id_or_username, get_users_with_subscriptions
from keyboards.inline import admin_panel_keyboard


class AdminStates(StatesGroup):
    waiting_trial_extension = State()


router = Router()


def _is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


async def _send_admin_panel(message: Message):
    await message.answer(
        "🛠 <b>Админ-панель</b>\n\nВыберите действие:",
        parse_mode="HTML",
        reply_markup=admin_panel_keyboard(),
    )


async def _send_users(message: Message):
    rows = await get_users_with_subscriptions()
    if not rows:
        await message.answer("Пользователей в базе пока нет.")
        return

    lines = ["👥 <b>Пользователи</b>"]
    for user_id, username, access_granted, subscription_until, trial_until, trial_feedback_sent in rows:
        name = f"@{username}" if username else "(без username)"
        if access_granted:
            status = "доступ открыт"
        elif subscription_until:
            status = "доступ открыт (legacy)"
        elif trial_until:
            feedback = ", отзыв запрошен" if trial_feedback_sent else ""
            status = f"trial до {trial_until}{feedback}"
        else:
            status = "доступа нет"
        lines.append(f"• {user_id} | {name} | {status}")

    text = "\n".join(lines)
    limit = 3500
    for i in range(0, len(text), limit):
        await message.answer(text[i : i + limit], parse_mode="HTML")


@router.message(Command("admin"))
async def admin_panel(message: Message):
    if not _is_admin(message.from_user.id):
        await message.answer("❌ Только админ может использовать эту команду.")
        return
    await _send_admin_panel(message)


@router.callback_query(F.data == "admin_add_trial")
async def admin_add_trial(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("❌ Только админ.", show_alert=True)
        return

    await callback.answer()
    await state.set_state(AdminStates.waiting_trial_extension)
    if callback.message:
        await callback.message.answer(
            "Введите пользователя и количество дней одним сообщением.\n\n"
            "Пример: <code>@username 3</code>\n"
            "Можно также по Telegram ID: <code>5011530740 3</code>",
            parse_mode="HTML",
        )


@router.callback_query(F.data == "admin_users")
async def admin_users(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("❌ Только админ.", show_alert=True)
        return

    await callback.answer()
    if callback.message:
        await _send_users(callback.message)


@router.message(AdminStates.waiting_trial_extension)
async def admin_trial_extension_entered(message: Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        await message.answer("❌ Только админ может использовать это действие.")
        await state.clear()
        return

    parts = (message.text or "").split()
    if len(parts) != 2:
        await message.answer("❌ Формат: @username 3 или telegram_id 3")
        return

    target_raw, days_raw = parts
    try:
        days = int(days_raw)
    except ValueError:
        await message.answer("❌ Количество дней должно быть числом.")
        return

    if days <= 0:
        await message.answer("❌ Количество дней должно быть больше нуля.")
        return

    user = await get_user_by_id_or_username(target_raw)
    if not user:
        await message.answer(
            "❌ Пользователь не найден. Он должен хотя бы один раз запустить бота через /start."
        )
        return

    new_trial_until = await add_trial_days(int(user["telegram_id"]), days)
    if not new_trial_until:
        await message.answer("❌ Не удалось продлить пробный период.")
        return

    await state.clear()
    username = f"@{user['username']}" if user["username"] else str(user["telegram_id"])
    until_text = new_trial_until.strftime("%d.%m.%Y %H:%M")

    await message.answer(f"✅ {username}: пробный период продлён до {until_text}.")
    try:
        await message.bot.send_message(
            chat_id=int(user["telegram_id"]),
            text=f"🎁 Админ продлил ваш пробный доступ до {until_text}.",
        )
    except Exception:
        await message.answer("⚠️ Пользователю не удалось отправить уведомление, но trial продлён.")
