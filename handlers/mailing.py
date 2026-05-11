from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from config import MIN_DELAY
from database import (
    check_subscription,
    get_previously_used_usernames,
    get_session,
    save_mailing,
)
from keyboards.inline import (
    duplicate_usernames_keyboard,
    mailing_control_keyboard,
    mailing_mode_keyboard,
    start_mailing_keyboard,
)
from telethon_manager import UserClient


class MailingStates(StatesGroup):
    text = State()
    mode = State()
    delay = State()
    usernames = State()
    duplicate_decision = State()
    confirm = State()


router = Router()
client_manager = UserClient()


def _normalize_usernames(raw_text: str) -> list[str]:
    normalized = []
    seen = set()
    for raw in raw_text.split():
        clean = raw.replace("@", "").strip()
        if not clean:
            continue
        lower = clean.lower()
        if lower in seen:
            continue
        seen.add(lower)
        normalized.append(clean)
    return normalized


def _extract_formatted_text(message: Message) -> str | None:
    # aiogram формирует HTML из entities, что позволяет сохранить жирный/курсив/и т.д.
    if getattr(message, "html_text", None):
        return message.html_text
    if message.text:
        return message.text
    return None


def _build_mailing_errors_report(failed: list[dict], rate_limit: dict | None = None) -> str:
    rate_limit = rate_limit or {}
    has_rate_limit = bool(rate_limit.get("detected"))
    if not failed and not has_rate_limit:
        return ""

    grouped = {}
    for item in failed:
        reason = item.get("reason", "неизвестная причина")
        grouped[reason] = grouped.get(reason, 0) + 1

    lines = ["⚠️ <b>Часть сообщений не отправлена</b>", ""]

    if has_rate_limit:
        max_wait = int(rate_limit.get("max_wait_seconds") or 0)
        events = int(rate_limit.get("events") or 0)
        recommended_delay = int(rate_limit.get("recommended_delay_seconds") or 0)
        max_applied_delay = int(rate_limit.get("max_applied_delay_seconds") or 0)
        lines.append("⏳ Обнаружен FloodWait от Telegram API.")
        lines.append("Это ограничение Telegram для аккаунта, а не ошибка бота.")
        if events > 0:
            lines.append(f"Срабатываний: {events}.")
        if max_wait > 0:
            lines.append(f"Максимальное ожидание: {max_wait} сек.")
        if max_applied_delay > 0:
            lines.append(f"Пиковая адаптивная задержка: {max_applied_delay} сек.")
        if recommended_delay > 0:
            lines.append(f"Рекомендуемая задержка для следующего запуска: от {recommended_delay} сек.")
        lines.append("")

    if failed:
        lines.append("Причины:")
        for reason, count in grouped.items():
            lines.append(f"• {count} — {reason}")

    if failed:
        examples = [f"@{item.get('username')}" for item in failed[:10] if item.get("username")]
        if examples:
            lines.extend(["", f"Примеры: {', '.join(examples)}"])
        if len(failed) > 10:
            lines.append(f"И еще {len(failed) - 10} username.")

    lines.extend(
        [
            "",
            "Что делать:",
            "• Увеличьте задержку и запускайте меньшие пачки.",
            "• Проверьте корректность username.",
            "• Если ограничение Telegram, подождите и повторите позже.",
        ]
    )
    return "\n".join(lines)


async def _move_to_confirm(message: Message, state: FSMContext, usernames: list[str]):
    data = await state.get_data()
    mode = data.get("mailing_mode", "send")
    mode_label = "📤 Отправка сообщений" if mode == "send" else "📝 Сохранение в черновики"

    await state.update_data(usernames=usernames)
    await state.set_state(MailingStates.confirm)
    await message.answer(
        f"Готово! Всего {len(usernames)} юзернеймов.\nРежим: {mode_label}",
        reply_markup=start_mailing_keyboard(),
    )


@router.message(Command(commands=["mailing"]))
async def start_mailing(message: Message, state: FSMContext):
    if not await check_subscription(message.from_user.id):
        return await message.answer("❌ У вас нет доступа к боту.")
    await state.set_state(MailingStates.text)
    await message.answer(
        "Введите текст для рассылки. Можно с форматированием: жирный, курсив, зачеркнутый, подчеркнутый и т.д."
    )


@router.message(MailingStates.text)
async def get_text(message: Message, state: FSMContext):
    formatted_text = _extract_formatted_text(message)
    if not formatted_text:
        await message.answer("❌ Отправьте текстовое сообщение для рассылки.")
        return

    await state.update_data(
        text=formatted_text,
        raw_text=(message.text or formatted_text),
        parse_mode="html",
    )
    await state.set_state(MailingStates.mode)
    await message.answer("Выберите режим рассылки:", reply_markup=mailing_mode_keyboard())


@router.callback_query(MailingStates.mode, F.data == "mail_mode_send")
async def set_mode_send(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(mailing_mode="send")
    await state.set_state(MailingStates.delay)
    await callback.message.answer(f"Введите задержку в секундах (мин {MIN_DELAY}):")


@router.callback_query(MailingStates.mode, F.data == "mail_mode_draft")
async def set_mode_draft(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(mailing_mode="draft")
    await state.set_state(MailingStates.delay)
    await callback.message.answer(
        f"Введите задержку между сохранением черновиков в секундах (мин {MIN_DELAY}):"
    )


@router.message(MailingStates.delay)
async def get_delay(message: Message, state: FSMContext):
    try:
        delay = int(message.text)
    except (TypeError, ValueError):
        await message.answer("❌ Введите число секунд.")
        return

    if delay < MIN_DELAY:
        return await message.answer(f"Минимальная задержка {MIN_DELAY} сек.")

    await state.update_data(delay=delay)
    await state.set_state(MailingStates.usernames)
    await message.answer("Введите юзернеймы через пробел:")


@router.message(MailingStates.usernames)
async def get_usernames(message: Message, state: FSMContext):
    usernames = _normalize_usernames(message.text or "")
    if not usernames:
        await message.answer("❌ Список пустой. Введите юзернеймы через пробел.")
        return

    previously_used = await get_previously_used_usernames(message.from_user.id)
    duplicates = [u for u in usernames if u.lower() in previously_used]

    await state.update_data(usernames=usernames, duplicate_usernames=duplicates)

    if duplicates:
        await state.set_state(MailingStates.duplicate_decision)
        preview = ", ".join(f"@{u}" for u in duplicates[:20])
        tail = "" if len(duplicates) <= 20 else f" и еще {len(duplicates) - 20}"
        await message.answer(
            "⚠️ В списке есть юзернеймы, которым вы уже делали рассылку:\n"
            f"{preview}{tail}\n\n"
            "Продолжить рассылку по ним?",
            reply_markup=duplicate_usernames_keyboard(),
        )
        return

    await _move_to_confirm(message, state, usernames)


@router.callback_query(F.data == "dups_yes")
async def duplicates_yes(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    usernames = data.get("usernames", [])
    if not usernames:
        await callback.message.answer("❌ Список не найден. Введите его заново.")
        await state.set_state(MailingStates.usernames)
        return
    await _move_to_confirm(callback.message, state, usernames)


@router.callback_query(F.data == "dups_edit_list")
async def duplicates_edit_list(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(MailingStates.usernames)
    await callback.message.answer("Введите новый список юзернеймов через пробел:")


@router.callback_query(F.data == "dups_skip_and_start")
async def duplicates_skip_and_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    usernames = data.get("usernames", [])
    duplicate_usernames = set(u.lower() for u in data.get("duplicate_usernames", []))

    filtered = [u for u in usernames if u.lower() not in duplicate_usernames]
    if not filtered:
        await state.set_state(MailingStates.usernames)
        await callback.message.answer(
            "❌ После исключения повторов список пустой. Введите новый список юзернеймов."
        )
        return

    await state.update_data(usernames=filtered)
    await _start_mailing_send(callback.message, state, actor_user_id=callback.from_user.id)


async def _start_mailing_send(message: Message, state: FSMContext, actor_user_id: int | None = None):
    data = await state.get_data()

    if "usernames" not in data or "text" not in data or "delay" not in data:
        return await message.answer(
            "❌ Данные для рассылки не найдены.\n"
            "Пожалуйста, сначала создайте рассылку через кнопку 'Создать рассылку'"
        )

    user_id = actor_user_id if actor_user_id is not None else message.from_user.id
    session = await get_session(user_id)
    if not session:
        return await message.answer("❌ Нет авторизованного аккаунта. Нажмите кнопку 'Авторизация'.")

    usernames = data["usernames"]
    text = data["text"]
    delay = data["delay"]
    mailing_mode = data.get("mailing_mode", "send")
    parse_mode = data.get("parse_mode")
    control_id = client_manager.create_mailing_control(
        owner_user_id=user_id,
        chat_id=message.chat.id,
    )
    control_message = await message.answer(
        "Управление рассылкой:",
        reply_markup=mailing_control_keyboard(control_id, paused=False),
    )

    try:
        if mailing_mode == "draft":
            result = await client_manager.save_bulk_drafts(
                session,
                usernames,
                text,
                delay,
                bot=message.bot,
                chat_id=message.chat.id,
                parse_mode=parse_mode,
                control_id=control_id,
            )
        else:
            result = await client_manager.send_bulk(
                session,
                usernames,
                text,
                delay,
                bot=message.bot,
                chat_id=message.chat.id,
                parse_mode=parse_mode,
                control_id=control_id,
            )
    finally:
        client_manager.finish_mailing_control(control_id)
        try:
            await control_message.edit_text("Управление рассылкой: завершено.", reply_markup=None)
        except Exception:
            pass

    successful_usernames = result.get("successful_usernames", []) if isinstance(result, dict) else []
    if successful_usernames:
        await save_mailing(user_id, text, delay, successful_usernames)
    await state.clear()
    if successful_usernames:
        done_text = (
            "✅ Рассылка завершена и в историю сохранены только успешно отправленные username!"
            if mailing_mode == "send"
            else "✅ Черновики сохранены, и в историю добавлены только реально сохранённые username!"
        )
    else:
        done_text = (
            "⚠️ Рассылка завершена, но ни один username не был успешно обработан, поэтому история не обновлялась."
            if mailing_mode == "send"
            else "⚠️ Операция завершена, но ни один черновик не был успешно сохранён, поэтому история не обновлялась."
        )
    await message.answer(done_text)

    failed = result.get("failed", []) if isinstance(result, dict) else []
    rate_limit = result.get("rate_limit") if isinstance(result, dict) else None
    report = _build_mailing_errors_report(failed, rate_limit=rate_limit)
    if report:
        await message.answer(report, parse_mode="HTML")


@router.message(Command(commands=["start_mailing"]))
async def start_mailing_send(message: Message, state: FSMContext):
    await _start_mailing_send(message, state, actor_user_id=message.from_user.id)


@router.callback_query(F.data == "start_mailing_now")
async def start_mailing_send_button(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await _start_mailing_send(callback.message, state, actor_user_id=callback.from_user.id)


@router.callback_query(F.data.startswith("mail_stop:"))
async def pause_mailing(callback: CallbackQuery):
    control_id = callback.data.split(":", 1)[1]
    control = client_manager.get_mailing_control(control_id)
    if not control or not control.get("active"):
        await callback.answer("Рассылка уже завершена.", show_alert=True)
        return
    if control.get("owner_user_id") != callback.from_user.id:
        await callback.answer("Этой рассылкой может управлять только тот, кто ее запустил.", show_alert=True)
        return

    client_manager.set_mailing_paused(control_id, True)
    await callback.message.edit_reply_markup(reply_markup=mailing_control_keyboard(control_id, paused=True))
    await callback.answer("Рассылка остановлена.")


@router.callback_query(F.data.startswith("mail_continue:"))
async def continue_mailing(callback: CallbackQuery):
    control_id = callback.data.split(":", 1)[1]
    control = client_manager.get_mailing_control(control_id)
    if not control or not control.get("active"):
        await callback.answer("Рассылка уже завершена.", show_alert=True)
        return
    if control.get("owner_user_id") != callback.from_user.id:
        await callback.answer("Этой рассылкой может управлять только тот, кто ее запустил.", show_alert=True)
        return

    client_manager.set_mailing_paused(control_id, False)
    await callback.message.edit_reply_markup(reply_markup=mailing_control_keyboard(control_id, paused=False))
    await callback.answer("Рассылка продолжена.")
