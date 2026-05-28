from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from config import ADMIN_ID, SUB_PRICE
from database import mark_trial_feedback_sent
from keyboards.inline import payment_keyboard, trial_feedback_keyboard


class TrialFeedbackStates(StatesGroup):
    waiting_reason = State()


router = Router()


async def send_trial_feedback_request(bot, user_id: int):
    await bot.send_message(
        chat_id=user_id,
        text=(
            "⌛ Ваш пробный период закончился.\n\n"
            "Как вам бот? Понравилось пользоваться?"
        ),
        reply_markup=trial_feedback_keyboard(),
    )
    await mark_trial_feedback_sent(user_id)


@router.callback_query(F.data == "trial_liked_yes")
async def trial_liked_yes(callback: CallbackQuery):
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "Рад, что бот оказался полезным.\n\n"
            f"Полный доступ стоит {SUB_PRICE} ₽. Нажмите кнопку ниже после оплаты, "
            "и админ подтвердит доступ.",
            reply_markup=payment_keyboard(),
        )


@router.callback_query(F.data == "trial_liked_no")
async def trial_liked_no(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(TrialFeedbackStates.waiting_reason)
    if callback.message:
        await callback.message.answer(
            "Понял. Расскажите, пожалуйста, почему бот не подошёл или чего не хватило?"
        )


@router.message(TrialFeedbackStates.waiting_reason)
async def trial_feedback_reason(message: Message, state: FSMContext):
    reason = (message.text or "").strip()
    if not reason:
        await message.answer("Отправьте, пожалуйста, текстом, что именно не понравилось.")
        return

    username = f"@{message.from_user.username}" if message.from_user.username else "(без username)"
    await message.bot.send_message(
        chat_id=ADMIN_ID,
        text=(
            "📩 Отзыв после пробного периода\n\n"
            f"Пользователь: {username}\n"
            f"ID: {message.from_user.id}\n\n"
            f"Причина:\n{reason}"
        ),
    )
    await state.clear()
    await message.answer("Спасибо за честный ответ. Я передал отзыв администратору.")
