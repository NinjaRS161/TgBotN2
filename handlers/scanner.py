import asyncio
import time

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, Message
from telethon.errors import RPCError

from database import check_subscription, get_session
from states.scan_states import ScanStates
from telethon_manager import UserClient
from utils.progress_bar import generate_progress

router = Router()
client_manager = UserClient()
MAX_POSTS_TO_SCAN = 0


async def _safe_edit_text(
    message: Message,
    text: str,
    *,
    parse_mode: str | None = None,
    state: dict | None = None,
    min_interval: float = 0.0,
):
    if not message:
        return

    if state is not None:
        last_text = state.get("text")
        if text == last_text:
            return
        last_ts = state.get("ts", 0.0)
        now = time.monotonic()
        if min_interval > 0 and (now - last_ts) < min_interval:
            return

    try:
        await message.edit_text(text, parse_mode=parse_mode)
        if state is not None:
            state["text"] = text
            state["ts"] = time.monotonic()
        return
    except TelegramRetryAfter as e:
        retry_after = getattr(e, "retry_after", None)
        if retry_after is None:
            retry_after = getattr(e, "seconds", None)
        if not retry_after:
            return
        if retry_after > 5:
            if state is not None:
                state["ts"] = time.monotonic()
            return
        try:
            await asyncio.sleep(float(retry_after))
            await message.edit_text(text, parse_mode=parse_mode)
            if state is not None:
                state["text"] = text
                state["ts"] = time.monotonic()
        except Exception:
            return
    except (TelegramNetworkError, TelegramBadRequest):
        return
    except Exception:
        return


def _normalize_channel_ref(raw_text: str) -> str:
    value = raw_text.strip()
    if value.startswith("t.me/"):
        return f"https://{value}"
    return value


def _normalize_usernames(raw_text: str) -> list[str]:
    normalized = []
    seen = set()
    for raw in raw_text.split():
        clean = raw.replace("@", "").strip()
        if not clean:
            continue
        low = clean.lower()
        if low in seen:
            continue
        seen.add(low)
        normalized.append(clean)
    return normalized


def _humanize_scan_error(error: Exception) -> str:
    name = error.__class__.__name__
    text = str(error)

    if name in {"AuthKeyUnregisteredError", "SessionRevokedError", "UnauthorizedError"}:
        return (
            "❌ Сессия Telegram недействительна.\n"
            "Пройдите авторизацию заново через кнопку 'Авторизация' (лучше через QR)."
        )

    if name in {"UsernameNotOccupiedError", "UsernameInvalidError"}:
        return (
            "❌ Канал или группа не найдены по этой ссылке/username.\n"
            "Проверьте формат: https://t.me/username"
        )

    if name in {"ChannelPrivateError", "ChatAdminRequiredError"}:
        return (
            "❌ Нет доступа к каналу/группе или комментариям.\n"
            "Убедитесь, что авторизованный аккаунт имеет доступ."
        )

    if name in {"InviteHashInvalidError", "InviteHashExpiredError"}:
        return "❌ Ссылка-приглашение недействительна или устарела."

    if name == "FloodWaitError":
        wait_seconds = getattr(error, "seconds", None)
        if isinstance(wait_seconds, int) and wait_seconds > 0:
            return f"⏳ Telegram временно ограничил запросы. Повторите через {wait_seconds} сек."
        return "⏳ Telegram временно ограничил запросы. Повторите позже."

    if isinstance(error, ValueError):
        return "❌ Не удалось разобрать ссылку. Отправьте ссылку в формате https://t.me/channel_username"

    if isinstance(error, RPCError):
        return f"❌ Ошибка Telegram API: {text}"

    return f"❌ Ошибка сканирования: {text}"


@router.message(F.text.in_(["Сканирование чатов", "🔎 Сканирование чатов"]))
async def start_scan_flow(message: Message, state: FSMContext):
    if not await get_session(message.from_user.id):
        await message.answer("❌ Сначала зарегистрируйтесь через кнопку 'Авторизация'.")
        return

    if not await check_subscription(message.from_user.id):
        await message.answer("❌ У вас нет доступа к боту.")
        return

    await state.set_state(ScanStates.waiting_channel_link)
    scan_scope_text = (
        "Будут просканированы комментарии ко всем доступным постам каналов "
        "или сообщения групп."
        if MAX_POSTS_TO_SCAN <= 0
        else (
            f"Будут просканированы комментарии к последним {MAX_POSTS_TO_SCAN} постам каналов "
            f"или последние {MAX_POSTS_TO_SCAN} сообщений групп."
        )
    )
    await message.answer(
        "🔎 Отправьте ссылку на Telegram-канал или публичную группу "
        "(например: https://t.me/username).\n"
        "Для каналов сканируются комментарии к постам, для групп — сообщения.\n"
        f"{scan_scope_text}"
    )


@router.message(Command(commands=["check_channels"]))
@router.message(F.text.in_(["Проверка каналов", "🧪 Проверка каналов"]))
async def start_channel_check_flow(message: Message, state: FSMContext):
    if not await get_session(message.from_user.id):
        await message.answer("❌ Сначала зарегистрируйтесь через кнопку 'Авторизация'.")
        return

    if not await check_subscription(message.from_user.id):
        await message.answer("❌ У вас нет доступа к боту.")
        return

    await state.set_state(ScanStates.waiting_usernames_for_channel_check)
    await message.answer(
        "🧪 Отправьте список username через пробел или с новой строки.\n"
        "Я отправлю 2 файла: исходный список и список со статусом наличия канала."
    )


@router.message(ScanStates.waiting_channel_link)
async def scan_channel_comments(message: Message, state: FSMContext):
    channel_ref = _normalize_channel_ref(message.text or "")
    if not channel_ref:
        await message.answer("❌ Отправьте корректную ссылку на канал.")
        return

    session = await get_session(message.from_user.id)
    if not session:
        await state.clear()
        await message.answer("❌ Сессия не найдена. Пройдите авторизацию заново.")
        return

    progress_message = await message.answer("⏳ Начинаю сканирование комментариев...")
    progress_state = {"ts": 0.0, "text": None}

    async def progress(scanned_count: int, usernames_count: int, scan_kind: str):
        if scanned_count == 0:
            return
        total_for_bar = MAX_POSTS_TO_SCAN if MAX_POSTS_TO_SCAN > 0 else max(scanned_count + 20, 20)
        bar = generate_progress(scanned_count, total_for_bar)
        scan_label = "Постов" if scan_kind == "posts" else "Сообщений"
        await _safe_edit_text(
            progress_message,
            (
                "🔎 <b>Сканирование в процессе...</b>\n\n"
                f"{bar}\n"
                f"📰 {scan_label} проверено: {scanned_count}\n"
                f"👤 Найдено username: {usernames_count}"
            ),
            parse_mode="HTML",
            state=progress_state,
            min_interval=1.5,
        )

    try:
        usernames, scanned_count, scan_kind = await client_manager.scan_channel_comment_usernames(
            session,
            channel_ref,
            post_limit=(None if MAX_POSTS_TO_SCAN <= 0 else MAX_POSTS_TO_SCAN),
            progress_callback=progress,
            progress_step=25,
        )
    except Exception as e:
        await state.clear()
        await _safe_edit_text(progress_message, _humanize_scan_error(e))
        return

    await state.clear()

    scan_label = "Постов" if scan_kind == "posts" else "Сообщений"

    if not usernames:
        done_bar = generate_progress(max(scanned_count, 1), max(scanned_count, 1))
        empty_text = (
            "👤 Username в комментариях не найдено."
            if scan_kind == "posts"
            else "👤 Username в сообщениях не найдено."
        )
        await _safe_edit_text(
            progress_message,
            (
                "✅ <b>Сканирование завершено</b>\n\n"
                f"{done_bar}\n"
                f"📰 {scan_label} просканировано: {scanned_count}\n"
                f"{empty_text}"
            ),
            parse_mode="HTML",
        )
        return

    lines = [f"@{username}" for username in usernames]
    payload = "\n".join(lines)
    txt_file = BufferedInputFile(payload.encode("utf-8"), filename="comment_usernames.txt")

    await _safe_edit_text(
        progress_message,
        "🧪 Проверяю, есть ли у найденных username личный Telegram-канал...",
    )

    check_state = {"checked": 0, "total": max(len(usernames), 1)}
    check_progress_state = {"ts": 0.0, "text": None}

    async def check_progress(checked_count: int, total_count: int):
        if checked_count == 0:
            return
        check_state["checked"] = checked_count
        check_state["total"] = max(total_count, 1)
        bar = generate_progress(checked_count, max(total_count, 1))
        await _safe_edit_text(
            progress_message,
            (
                "🧪 <b>Проверка каналов в процессе...</b>\n\n"
                f"{bar}\n"
                f"👤 Проверено username: {checked_count}/{total_count}"
            ),
            parse_mode="HTML",
            state=check_progress_state,
            min_interval=1.5,
        )

    async def check_wait(
        checked_count: int,
        total_count: int,
        username: str,
        value: int,
        reason: str,
    ):
        current_checked = max(checked_count, check_state["checked"])
        current_total = max(total_count, check_state["total"])
        bar = generate_progress(current_checked, max(current_total, 1))
        if reason == "floodwait":
            wait_text = f"⏳ Лимит Telegram: ожидание {value} сек. (на @{username})"
        elif reason == "floodwait_skip":
            wait_text = f"⏭ Пропущен @{username}: слишком большой FloodWait ({value} сек)"
        else:
            wait_text = f"🔌 Проблема сети: переподключение, попытка {value}/3 (на @{username})"
        await _safe_edit_text(
            progress_message,
            (
                "🧪 <b>Проверка каналов в процессе...</b>\n\n"
                f"{bar}\n"
                f"👤 Проверено username: {current_checked}/{current_total}\n"
                f"{wait_text}"
            ),
            parse_mode="HTML",
            state=check_progress_state,
            min_interval=1.5,
        )

    statuses = await client_manager.check_usernames_channel_status(
        session,
        usernames,
        progress_callback=check_progress,
        wait_callback=check_wait,
        progress_step=10,
    )

    status_lines = ["Юзернеймы имеющие телеграмм каналы:"]
    status_lines.extend(
        f"@{item['username']}"
        for item in statuses
        if item.get("has_channel") is True
    )
    status_payload = "\n".join(status_lines)
    status_txt_file = BufferedInputFile(
        status_payload.encode("utf-8"),
        filename="comment_usernames_with_channel_status.txt",
    )

    has_channel_count = sum(1 for item in statuses if item.get("has_channel") is True)
    no_channel_count = sum(1 for item in statuses if item.get("has_channel") is False)
    unknown_count = sum(1 for item in statuses if item.get("has_channel") is None)

    done_bar = generate_progress(max(scanned_count, 1), max(scanned_count, 1))
    await _safe_edit_text(
        progress_message,
        (
            "✅ <b>Сканирование завершено</b>\n\n"
            f"{done_bar}\n"
            f"📰 {scan_label} просканировано: {scanned_count}\n"
            f"👤 Уникальных username: {len(usernames)}\n"
            f"📢 Канал есть: {has_channel_count}\n"
            f"🙅 Канала нет: {no_channel_count}\n"
            f"❓ Не определено: {unknown_count}"
        ),
        parse_mode="HTML",
    )
    await message.answer_document(txt_file, caption="📄 Список username из комментариев")
    await message.answer_document(
        status_txt_file,
        caption="📄 Список username со статусом наличия канала",
    )


@router.message(ScanStates.waiting_usernames_for_channel_check)
async def check_usernames_channel_status(message: Message, state: FSMContext):
    usernames = _normalize_usernames(message.text or "")
    if not usernames:
        await message.answer("❌ Список пустой. Отправьте username через пробел или с новой строки.")
        return

    session = await get_session(message.from_user.id)
    if not session:
        await state.clear()
        await message.answer("❌ Сессия не найдена. Пройдите авторизацию заново.")
        return

    progress_message = await message.answer("🧪 Проверяю наличие каналов у username...")
    check_progress_state = {"ts": 0.0, "text": None}

    check_state = {"checked": 0, "total": max(len(usernames), 1)}

    async def check_progress(checked_count: int, total_count: int):
        if checked_count == 0:
            return
        check_state["checked"] = checked_count
        check_state["total"] = max(total_count, 1)
        bar = generate_progress(checked_count, max(total_count, 1))
        await _safe_edit_text(
            progress_message,
            (
                "🧪 <b>Проверка в процессе...</b>\n\n"
                f"{bar}\n"
                f"👤 Проверено username: {checked_count}/{total_count}"
            ),
            parse_mode="HTML",
            state=check_progress_state,
            min_interval=1.5,
        )

    async def check_wait(
        checked_count: int,
        total_count: int,
        username: str,
        value: int,
        reason: str,
    ):
        current_checked = max(checked_count, check_state["checked"])
        current_total = max(total_count, check_state["total"])
        bar = generate_progress(current_checked, max(current_total, 1))
        if reason == "floodwait":
            wait_text = f"⏳ Лимит Telegram: ожидание {value} сек. (на @{username})"
        elif reason == "floodwait_skip":
            wait_text = f"⏭ Пропущен @{username}: слишком большой FloodWait ({value} сек)"
        else:
            wait_text = f"🔌 Проблема сети: переподключение, попытка {value}/3 (на @{username})"
        await _safe_edit_text(
            progress_message,
            (
                "🧪 <b>Проверка в процессе...</b>\n\n"
                f"{bar}\n"
                f"👤 Проверено username: {current_checked}/{current_total}\n"
                f"{wait_text}"
            ),
            parse_mode="HTML",
            state=check_progress_state,
            min_interval=1.5,
        )

    try:
        statuses = await client_manager.check_usernames_channel_status(
            session,
            usernames,
            progress_callback=check_progress,
            wait_callback=check_wait,
            progress_step=10,
        )
    except Exception as e:
        await state.clear()
        await _safe_edit_text(progress_message, _humanize_scan_error(e))
        return

    await state.clear()

    plain_lines = [f"@{username}" for username in usernames]
    plain_payload = "\n".join(plain_lines)
    plain_txt_file = BufferedInputFile(plain_payload.encode("utf-8"), filename="input_usernames.txt")

    status_lines = ["Юзернеймы имеющие телеграмм каналы:"]
    status_lines.extend(
        f"@{item['username']}"
        for item in statuses
        if item.get("has_channel") is True
    )
    status_payload = "\n".join(status_lines)
    status_txt_file = BufferedInputFile(
        status_payload.encode("utf-8"),
        filename="input_usernames_with_channel_status.txt",
    )

    has_channel_count = sum(1 for item in statuses if item.get("has_channel") is True)
    no_channel_count = sum(1 for item in statuses if item.get("has_channel") is False)
    unknown_count = sum(1 for item in statuses if item.get("has_channel") is None)

    done_bar = generate_progress(max(len(statuses), 1), max(len(statuses), 1))
    await _safe_edit_text(
        progress_message,
        (
            "✅ <b>Проверка завершена</b>\n\n"
            f"{done_bar}\n"
            f"👤 Всего username: {len(usernames)}\n"
            f"📢 Канал есть: {has_channel_count}\n"
            f"🙅 Канала нет: {no_channel_count}\n"
            f"❓ Не определено: {unknown_count}"
        ),
        parse_mode="HTML",
    )
    await message.answer_document(plain_txt_file, caption="📄 Исходный список username")
    await message.answer_document(
        status_txt_file,
        caption="📄 Список username со статусом наличия канала",
    )
