from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from config import ADMIN_ID, SUB_PRICE
from database import (
    approve_payment as approve_payment_record,
    create_payment,
    get_session,
    get_users_with_subscriptions,
    reject_payment as reject_payment_record,
    save_user_profile,
)
from keyboards.inline import admin_approve_keyboard, payment_keyboard
from keyboards.premium_menu import premium_reply_menu

router = Router()


@router.message(F.text == "/subscribe")
async def subscribe(message: Message):
    await save_user_profile(message.from_user.id, message.from_user.username)

    if not await get_session(message.from_user.id):
        return await message.answer("❌ Сначала зарегистрируйтесь через кнопку 'Авторизация'.")

    await message.answer(
        f"💳 Доступ к боту стоит {SUB_PRICE} ₽.\n\n"
        f"Это разовая покупка без срока окончания.\n"
        f"Переведите сумму на мои реквизиты: 89508543308(Сбербанк) и нажмите кнопку ниже:",
        reply_markup=payment_keyboard(),
    )


@router.callback_query(F.data == "paid")
async def user_paid(callback: CallbackQuery):
    user_id = callback.from_user.id
    await save_user_profile(user_id, callback.from_user.username)

    if not await get_session(user_id):
        await callback.answer("Сначала зарегистрируйтесь", show_alert=True)
        await callback.bot.send_message(user_id, "❌ Сначала зарегистрируйтесь через кнопку 'Авторизация'.")
        return

    payment_id = await create_payment(user_id, SUB_PRICE)
    await callback.answer("💳 Оплата отмечена, ждите подтверждения админа", show_alert=True)

    await callback.bot.send_message(
        chat_id=ADMIN_ID,
        text=(
            f"📢 Пользователь @{callback.from_user.username} ({user_id}) отметил оплату.\n"
            f"💳 Сумма: {SUB_PRICE} ₽\n"
            f"🧾 Платеж ID: {payment_id}"
        ),
        reply_markup=admin_approve_keyboard(payment_id),
    )


@router.callback_query(F.data.startswith("approve_payment:"))
async def approve_payment(callback: CallbackQuery):
    admin_id = callback.from_user.id
    if admin_id != ADMIN_ID:
        return await callback.answer("❌ Только админ может подтверждать оплату.", show_alert=True)

    payment_id = int(callback.data.split(":", 1)[1])
    user_id = await approve_payment_record(payment_id)
    if not user_id:
        return await callback.answer("❌ Платеж не найден или уже обработан.", show_alert=True)

    await callback.bot.send_message(
        chat_id=user_id,
        text="✅ Оплата подтверждена! Доступ к боту открыт, теперь вы можете создавать рассылки.",
        reply_markup=premium_reply_menu(has_access=True),
    )

    await callback.answer("Доступ открыт!")


@router.callback_query(F.data.startswith("reject_payment:"))
async def reject_payment(callback: CallbackQuery):
    admin_id = callback.from_user.id
    if admin_id != ADMIN_ID:
        return await callback.answer("❌ Только админ может отклонять оплату.", show_alert=True)

    payment_id = int(callback.data.split(":", 1)[1])
    user_id = await reject_payment_record(payment_id)
    if not user_id:
        return await callback.answer("❌ Платеж не найден или уже обработан.", show_alert=True)

    await callback.bot.send_message(
        chat_id=user_id,
        text="❌ Оплата не подтверждена. Пожалуйста, свяжитесь с админом.",
    )

    await callback.answer("Оплата отклонена!")


@router.message(Command("users"))
async def list_users(message: Message):
    if message.from_user.id != ADMIN_ID:
        return await message.answer("❌ Только админ может использовать эту команду.")

    rows = await get_users_with_subscriptions()
    if not rows:
        return await message.answer("Пользователей в базе пока нет.")

    lines = ["👥 <b>Пользователи</b>"]

    for user_id, username, access_granted, subscription_until in rows:
        name = f"@{username}" if username else "(без username)"
        if access_granted:
            lines.append(f"• {user_id} | {name} | доступ открыт")
        elif subscription_until:
            lines.append(f"• {user_id} | {name} | доступ открыт (legacy)")
        else:
            lines.append(f"• {user_id} | {name} | доступа нет")

    text = "\n".join(lines)
    limit = 3500
    for i in range(0, len(text), limit):
        chunk = text[i : i + limit]
        await message.answer(chunk, parse_mode="HTML")
