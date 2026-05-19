from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from database import (
    check_subscription,
    create_account_invite,
    find_user_by_username,
    get_account_invite,
    get_session,
    grant_access,
    save_user_profile,
    set_account_invite_status,
)
from keyboards.inline import account_invite_keyboard
from keyboards.premium_menu import premium_reply_menu


class AddAccountStates(StatesGroup):
    waiting_username = State()


router = Router()


def _normalize_username(raw_text: str | None) -> str:
    return (raw_text or "").strip().lstrip("@")


async def _start_add_account(
    message: Message,
    state: FSMContext,
    user_id: int | None = None,
    username: str | None = None,
):
    resolved_user_id = user_id if user_id is not None else message.from_user.id
    resolved_username = username if username is not None else message.from_user.username

    await save_user_profile(resolved_user_id, resolved_username)

    if not await check_subscription(resolved_user_id):
        await message.answer("❌ Добавлять аккаунты можно только после оплаты доступа.")
        return

    if not await get_session(resolved_user_id):
        await message.answer("❌ Сначала авторизуйте основной аккаунт через кнопку 'Авторизация'.")
        return

    await state.set_state(AddAccountStates.waiting_username)
    await message.answer(
        "Введите username второго аккаунта без ссылки или с @.\n\n"
        "Важно: второй аккаунт должен сначала запустить этого бота и пройти авторизацию."
    )


@router.message(F.text.in_(["Добавить аккаунт", "➕ Добавить аккаунт"]))
async def add_account_button(message: Message, state: FSMContext):
    await _start_add_account(message, state)


@router.callback_query(F.data == "add_account")
async def add_account_callback(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if callback.message:
        await _start_add_account(
            callback.message,
            state,
            user_id=callback.from_user.id,
            username=callback.from_user.username,
        )


@router.message(AddAccountStates.waiting_username)
async def add_account_username(message: Message, state: FSMContext):
    username = _normalize_username(message.text)
    if not username:
        await message.answer("❌ Введите username второго аккаунта.")
        return

    if message.from_user.username and username.lower() == message.from_user.username.lower():
        await message.answer("❌ Это ваш текущий аккаунт. Введите username второго аккаунта.")
        return

    target = await find_user_by_username(username)
    if not target:
        await message.answer(
            "❌ Этот аккаунт пока не найден в боте.\n\n"
            f"Попросите @{username} сначала нажать /start у этого бота, "
            "затем пройти авторизацию и после этого повторите добавление."
        )
        return

    target_id = int(target["telegram_id"])
    if target_id == message.from_user.id:
        await message.answer("❌ Это ваш текущий аккаунт. Введите username второго аккаунта.")
        return

    if not target["has_session"]:
        await message.answer(
            f"❌ @{target['username'] or username} уже запускал бота, но ещё не авторизовался.\n\n"
            "Попросите его пройти авторизацию через кнопку 'Авторизация', затем повторите добавление."
        )
        return

    invite_id = await create_account_invite(message.from_user.id, target_id)
    owner_name = f"@{message.from_user.username}" if message.from_user.username else str(message.from_user.id)

    try:
        await message.bot.send_message(
            chat_id=target_id,
            text=(
                f"{owner_name} приглашает вас подключиться к его тарифу.\n\n"
                "Если вы согласны, подтвердите приглашение кнопкой ниже."
            ),
            reply_markup=account_invite_keyboard(invite_id),
        )
    except Exception:
        await message.answer(
            f"❌ Не удалось отправить приглашение @{target['username'] or username}.\n"
            "Проверьте, что второй аккаунт не заблокировал бота и уже нажимал /start."
        )
        return

    await state.clear()
    await message.answer(
        f"✅ Приглашение отправлено @{target['username'] or username}.\n"
        "Доступ откроется после подтверждения со второго аккаунта."
    )


@router.callback_query(F.data.startswith("account_invite_accept:"))
async def accept_account_invite(callback: CallbackQuery):
    invite_id = int(callback.data.split(":", 1)[1])
    invite = await get_account_invite(invite_id)
    if not invite or invite["status"] != "pending":
        await callback.answer("Приглашение уже обработано или не найдено.", show_alert=True)
        return

    if int(invite["invited_id"]) != callback.from_user.id:
        await callback.answer("Это приглашение отправлено другому аккаунту.", show_alert=True)
        return

    if not await get_session(callback.from_user.id):
        await callback.answer("Сначала пройдите авторизацию в боте.", show_alert=True)
        return

    if not await check_subscription(int(invite["owner_id"])):
        await callback.answer("У владельца тарифа сейчас нет активного доступа.", show_alert=True)
        return

    if not await set_account_invite_status(invite_id, "accepted"):
        await callback.answer("Приглашение уже обработано.", show_alert=True)
        return

    await grant_access(callback.from_user.id, callback.from_user.username)
    await callback.answer("Доступ открыт!")

    if callback.message:
        await callback.message.edit_text("✅ Приглашение принято. Доступ к боту открыт.")

    await callback.bot.send_message(
        chat_id=int(invite["owner_id"]),
        text=(
            f"✅ @{callback.from_user.username or callback.from_user.id} подтвердил приглашение. "
            "Аккаунт добавлен к вашему тарифу."
        ),
    )
    await callback.bot.send_message(
        chat_id=callback.from_user.id,
        text="Теперь вы можете пользоваться функциями бота.",
        reply_markup=premium_reply_menu(has_access=True),
    )


@router.callback_query(F.data.startswith("account_invite_reject:"))
async def reject_account_invite(callback: CallbackQuery):
    invite_id = int(callback.data.split(":", 1)[1])
    invite = await get_account_invite(invite_id)
    if not invite or invite["status"] != "pending":
        await callback.answer("Приглашение уже обработано или не найдено.", show_alert=True)
        return

    if int(invite["invited_id"]) != callback.from_user.id:
        await callback.answer("Это приглашение отправлено другому аккаунту.", show_alert=True)
        return

    if not await set_account_invite_status(invite_id, "rejected"):
        await callback.answer("Приглашение уже обработано.", show_alert=True)
        return

    await callback.answer("Приглашение отклонено.")
    if callback.message:
        await callback.message.edit_text("❌ Приглашение отклонено.")

    await callback.bot.send_message(
        chat_id=int(invite["owner_id"]),
        text=f"❌ @{callback.from_user.username or callback.from_user.id} отклонил приглашение.",
    )
