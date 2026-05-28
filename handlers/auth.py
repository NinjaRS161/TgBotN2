import asyncio
from io import BytesIO
from uuid import uuid4

from aiogram import F, Router
from aiogram.filters import Command, Filter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from telethon import TelegramClient
from telethon.errors import (
    AuthTokenExpiredError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)
from telethon.sessions import StringSession

from config import API_HASH, API_ID, TRIAL_DAYS
from database import save_session, save_user_profile, start_trial_if_needed
from states.auth_states import AuthStates

try:
    import qrcode
except ImportError:
    qrcode = None


class CallbackDataFilter(Filter):
    def __init__(self, data: str):
        self.data = data

    async def __call__(self, callback: CallbackQuery):
        return callback.data == self.data


router = Router()
_pending_auth_clients: dict[str, TelegramClient] = {}


def _store_auth_client(client: TelegramClient) -> str:
    auth_id = uuid4().hex
    _pending_auth_clients[auth_id] = client
    return auth_id


def _get_auth_client(auth_id: str | None) -> TelegramClient | None:
    if not auth_id:
        return None
    return _pending_auth_clients.get(auth_id)


async def _drop_auth_client(auth_id: str | None) -> None:
    client = _pending_auth_clients.pop(auth_id, None) if auth_id else None
    if not client:
        return
    try:
        await client.disconnect()
    except Exception:
        return


async def _try_delete_message(bot, chat_id: int, message_id: int) -> None:
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        return


async def _schedule_delete_message(
    bot,
    chat_id: int,
    message_id: int,
    delay_seconds: int = 130,
) -> None:
    await asyncio.sleep(delay_seconds)
    await _try_delete_message(bot, chat_id, message_id)


def resend_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Отправить код заново", callback_data="resend_code")]
        ]
    )


async def _send_qr_message(message: Message, qr_url: str, *, refreshed: bool = False):
    caption = (
        "📱 Откройте Telegram на телефоне: Настройки -> Устройства -> "
        "Сканировать QR, затем отсканируйте код. Время ожидания: 2 минуты."
    )
    if refreshed:
        caption = "🔄 QR-код был обновлён. Отсканируйте новый код.\n\n" + caption

    if not qrcode:
        fallback_message = await message.answer(
            "QR-картинка недоступна на сервере, но вход можно завершить по кнопке ниже.\n\n"
            "Откройте её на телефоне, где уже выполнен вход в Telegram.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="Открыть вход в Telegram", url=qr_url)]
                ]
            ),
        )
        asyncio.create_task(
            _schedule_delete_message(
                message.bot,
                fallback_message.chat.id,
                fallback_message.message_id,
            )
        )
        return fallback_message

    img = qrcode.make(qr_url)
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    qr_file = BufferedInputFile(buffer.getvalue(), filename="login_qr.png")

    qr_message = await message.answer_photo(
        photo=qr_file,
        caption=caption,
    )
    asyncio.create_task(
        _schedule_delete_message(message.bot, qr_message.chat.id, qr_message.message_id)
    )
    return qr_message


async def _finish_login(
    message: Message,
    state: FSMContext,
    client: TelegramClient,
    text: str,
    user_id: int | None = None,
    username: str | None = None,
):
    session_str = client.session.save()
    resolved_user_id = user_id if user_id is not None else message.from_user.id
    resolved_username = username if username is not None else message.from_user.username
    data = await state.get_data()
    auth_id = data.get("auth_id")

    await save_session(resolved_user_id, session_str, resolved_username)
    await message.answer(text)
    trial = await start_trial_if_needed(resolved_user_id, resolved_username)
    if trial and trial["status"] == "trial" and trial["is_new"]:
        trial_until = trial["trial_until"].strftime("%d.%m.%Y %H:%M")
        await message.answer(
            f"🎁 Вам открыт пробный доступ на {TRIAL_DAYS} дня.\n"
            f"Он действует до {trial_until}."
        )
    await _drop_auth_client(auth_id)
    await state.clear()


async def _start_phone_login(
    message: Message,
    state: FSMContext,
    user_id: int | None = None,
    username: str | None = None,
):
    resolved_user_id = user_id if user_id is not None else message.from_user.id
    resolved_username = username if username is not None else message.from_user.username

    await save_user_profile(resolved_user_id, resolved_username)
    await state.set_state(AuthStates.waiting_for_phone)
    await state.update_data(
        auth_method="phone",
        auth_user_id=resolved_user_id,
        auth_username=resolved_username,
    )
    await message.answer("Введите номер телефона в формате +79161234567:")


async def _start_qr_login(
    message: Message,
    state: FSMContext,
    user_id: int | None = None,
    username: str | None = None,
):
    resolved_user_id = user_id if user_id is not None else message.from_user.id
    resolved_username = username if username is not None else message.from_user.username
    previous_auth_id = (await state.get_data()).get("auth_id")
    await _drop_auth_client(previous_auth_id)

    await save_user_profile(resolved_user_id, resolved_username)
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()
    auth_id = _store_auth_client(client)

    try:
        qr_login = await client.qr_login()
        await state.update_data(
            auth_id=auth_id,
            auth_method="qr",
            auth_user_id=resolved_user_id,
            auth_username=resolved_username,
        )
        qr_message = await _send_qr_message(message, qr_login.url)

        while True:
            try:
                await qr_login.wait(timeout=120)
                break
            except AuthTokenExpiredError:
                if "qr_message" in locals():
                    await _try_delete_message(message.bot, qr_message.chat.id, qr_message.message_id)
                await qr_login.recreate()
                qr_message = await _send_qr_message(message, qr_login.url, refreshed=True)
    except SessionPasswordNeededError:
        if "qr_message" in locals():
            await _try_delete_message(message.bot, qr_message.chat.id, qr_message.message_id)
        await state.set_state(AuthStates.waiting_for_2fa)
        await message.answer("У вас включена 2FA. Введите пароль Telegram:")
        return
    except asyncio.TimeoutError:
        if "qr_message" in locals():
            await _try_delete_message(message.bot, qr_message.chat.id, qr_message.message_id)
        await message.answer("⌛ QR-код истек. Нажмите кнопку 'Авторизация' и выберите QR снова")
        await _drop_auth_client(auth_id)
        await state.clear()
        return
    except Exception as e:
        if "qr_message" in locals():
            await _try_delete_message(message.bot, qr_message.chat.id, qr_message.message_id)
        await message.answer(f"❌ Ошибка QR-авторизации: {e}")
        await _drop_auth_client(auth_id)
        await state.clear()
        return

    if "qr_message" in locals():
        await _try_delete_message(message.bot, qr_message.chat.id, qr_message.message_id)
    await _finish_login(
        message,
        state,
        client,
        "✅ Авторизация через QR успешна! Теперь вы можете делать рассылку через меню.",
        user_id=resolved_user_id,
        username=resolved_username,
    )


@router.message(Command(commands=["login"]))
async def login_start(message: Message, state: FSMContext):
    await _start_phone_login(message, state)


@router.message(Command(commands=["login_qr"]))
async def login_qr_start(message: Message, state: FSMContext):
    await _start_qr_login(message, state)


@router.callback_query(F.data == "auth_phone")
async def login_phone_button(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not callback.message:
        return
    await _start_phone_login(
        callback.message,
        state,
        user_id=callback.from_user.id,
        username=callback.from_user.username,
    )


@router.callback_query(F.data == "auth_qr")
async def login_qr_button(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not callback.message:
        return
    await _start_qr_login(
        callback.message,
        state,
        user_id=callback.from_user.id,
        username=callback.from_user.username,
    )


@router.message(AuthStates.waiting_for_phone)
async def phone_entered(message: Message, state: FSMContext):
    phone = message.text.strip()
    if not phone.startswith("+") or len(phone) < 10:
        return await message.answer("❌ Введите корректный номер телефона в формате +79161234567")

    previous_auth_id = (await state.get_data()).get("auth_id")
    await _drop_auth_client(previous_auth_id)
    await state.update_data(phone=phone)

    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()
    auth_id = _store_auth_client(client)

    try:
        sent_code = await client.send_code_request(phone)
        await state.update_data(auth_id=auth_id, phone_code_hash=sent_code.phone_code_hash)
        await state.set_state(AuthStates.waiting_for_code)
        await message.answer(
            "✅ Код отправлен. Введите код из Telegram:",
            reply_markup=resend_keyboard(),
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка отправки кода: {e}")
        await _drop_auth_client(auth_id)


@router.callback_query(CallbackDataFilter("resend_code"))
async def resend_code(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    phone = data.get("phone")
    auth_id = data.get("auth_id")
    client = _get_auth_client(auth_id)
    if not phone or not client:
        await callback.message.answer("❌ Ошибка. Нажмите кнопку 'Авторизация' и начните заново")
        await _drop_auth_client(auth_id)
        await state.clear()
        return

    try:
        sent_code = await client.send_code_request(phone)
        await state.update_data(phone_code_hash=sent_code.phone_code_hash)
        await callback.message.answer("🔄 Новый код отправлен. Введите его:")
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка повторной отправки кода: {e}")


@router.message(AuthStates.waiting_for_code)
async def code_entered(message: Message, state: FSMContext):
    data = await state.get_data()
    phone = data.get("phone")
    auth_id = data.get("auth_id")
    client = _get_auth_client(auth_id)
    phone_code_hash = data.get("phone_code_hash")
    auth_user_id = data.get("auth_user_id", message.from_user.id)
    auth_username = data.get("auth_username", message.from_user.username)
    code = "".join(ch for ch in message.text if ch.isdigit())

    if not phone or not client or not phone_code_hash:
        await message.answer("❌ Сессия авторизации истекла. Нажмите кнопку 'Авторизация' и начните заново")
        await state.clear()
        return

    try:
        await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
    except PhoneCodeExpiredError:
        await message.answer(
            "❌ Этот код больше нельзя использовать (истек или заблокирован Telegram). "
            "Нажмите кнопку 'Авторизация' и получите новый код."
        )
        await _drop_auth_client(auth_id)
        await state.clear()
        return
    except PhoneCodeInvalidError:
        await message.answer("❌ Неверный код. Проверьте код и попробуйте снова.")
        return
    except SessionPasswordNeededError:
        await state.set_state(AuthStates.waiting_for_2fa)
        await message.answer("У вас включена 2FA. Введите пароль Telegram:")
        return
    except Exception as e:
        await message.answer(f"❌ Ошибка авторизации: {e}")
        await _drop_auth_client(auth_id)
        await state.clear()
        return

    await _finish_login(
        message,
        state,
        client,
        "✅ Авторизация успешна! Теперь вы можете делать рассылку через меню.",
        user_id=auth_user_id,
        username=auth_username,
    )


@router.message(AuthStates.waiting_for_2fa)
async def password_2fa(message: Message, state: FSMContext):
    data = await state.get_data()
    auth_id = data.get("auth_id")
    client = _get_auth_client(auth_id)
    auth_method = data.get("auth_method")
    auth_user_id = data.get("auth_user_id", message.from_user.id)
    auth_username = data.get("auth_username", message.from_user.username)
    password = message.text.strip()

    if not client:
        await message.answer("❌ Сессия авторизации истекла. Нажмите кнопку 'Авторизация' и начните заново")
        await state.clear()
        return

    try:
        await client.sign_in(password=password)
    except Exception as e:
        await message.answer(f"❌ Ошибка 2FA: {e}")
        await _drop_auth_client(auth_id)
        await state.clear()
        return

    success_text = (
        "✅ Авторизация через QR + 2FA успешна! Теперь вы можете делать рассылку через меню."
        if auth_method == "qr"
        else "✅ Авторизация с 2FA успешна! Теперь вы можете делать рассылку через меню."
    )
    await _finish_login(
        message,
        state,
        client,
        success_text,
        user_id=auth_user_id,
        username=auth_username,
    )
