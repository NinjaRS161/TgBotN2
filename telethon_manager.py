import asyncio
import math
import random

from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.sessions import StringSession
from telethon.tl.functions.messages import SaveDraftRequest
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.types import Channel, Chat, PeerChannel, User
from telethon.utils import sanitize_parse_mode

from config import (
    ADAPTIVE_DELAY_DECAY_EVERY_SUCCESS,
    ADAPTIVE_DELAY_JITTER_SECONDS,
    ADAPTIVE_DELAY_MAX_SECONDS,
    ADAPTIVE_DELAY_STEP_SECONDS,
    API_HASH,
    API_ID,
    FLOODWAIT_DELAY_SCALE_DIVISOR,
    FLOODWAIT_EXTRA_DELAY_SECONDS,
    MAX_FLOODWAIT_SECONDS,
)
from utils.progress_bar import generate_progress


class UserClient:
    def __init__(self):
        self.api_id = API_ID
        self.api_hash = API_HASH
        self._mailing_controls: dict[str, dict] = {}
        self._mailing_control_seq = 0
        self._scan_sender_resolve_limit = 5
        self._scan_chunk_size = 100

    async def create_client(self, session_string=None):
        return TelegramClient(
            StringSession(session_string) if session_string else StringSession(),
            self.api_id,
            self.api_hash,
        )

    async def get_client(self, session):
        return await self.create_client(session)

    def _is_timeout_error(self, error: Exception) -> bool:
        if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
            return True
        text = str(error).lower()
        return "timeout" in text or "timed out" in text

    async def _safe_get_sender(self, message, timeout_seconds: int = 15):
        try:
            return await asyncio.wait_for(message.get_sender(), timeout=timeout_seconds)
        except FloodWaitError as e:
            await asyncio.sleep(max(int(getattr(e, "seconds", 0)), 1))
            try:
                return await asyncio.wait_for(message.get_sender(), timeout=timeout_seconds)
            except Exception:
                return None
        except Exception:
            return None

    def _remember_username(self, usernames_map: dict[str, str], username: str | None) -> None:
        if username:
            usernames_map[username.lower()] = username

    async def _resolve_message_username(
        self,
        message,
        sender_cache: dict[int, str | None],
        semaphore: asyncio.Semaphore,
    ) -> str | None:
        sender_id = getattr(message, "sender_id", None)
        sender = getattr(message, "sender", None)
        direct_username = getattr(sender, "username", None) if sender else None

        if sender_id:
            if sender_id in sender_cache:
                cached_username = sender_cache[sender_id]
                return cached_username or direct_username
            if sender is not None:
                sender_cache[sender_id] = direct_username
                return direct_username
        elif direct_username:
            return direct_username

        if not sender_id:
            return None

        async with semaphore:
            sender = await self._safe_get_sender(message)

        username = getattr(sender, "username", None) if sender else None
        sender_cache[sender_id] = username
        return username

    async def _flush_scan_chunk(
        self,
        messages: list,
        usernames_map: dict[str, str],
        sender_cache: dict[int, str | None],
        semaphore: asyncio.Semaphore,
    ) -> None:
        if not messages:
            return

        pending_messages = []
        seen_pending_sender_ids = set()

        for message in messages:
            sender = getattr(message, "sender", None)
            username = getattr(sender, "username", None) if sender else None
            sender_id = getattr(message, "sender_id", None)

            if username:
                self._remember_username(usernames_map, username)
                if sender_id:
                    sender_cache[sender_id] = username
                continue

            if not sender_id:
                continue

            if sender_id in sender_cache:
                self._remember_username(usernames_map, sender_cache[sender_id])
                continue

            if sender_id in seen_pending_sender_ids:
                continue

            seen_pending_sender_ids.add(sender_id)
            pending_messages.append(message)

        if not pending_messages:
            return

        resolved_usernames = await asyncio.gather(
            *(
                self._resolve_message_username(message, sender_cache, semaphore)
                for message in pending_messages
            ),
            return_exceptions=True,
        )

        for username in resolved_usernames:
            if isinstance(username, Exception):
                continue
            self._remember_username(usernames_map, username)

    async def _connect_with_retry(self, client, max_attempts: int = 3, base_delay: int = 2) -> None:
        last_error = None
        for attempt in range(1, max_attempts + 1):
            try:
                await client.connect()
                return
            except Exception as e:
                last_error = e
                if not self._is_timeout_error(e) or attempt >= max_attempts:
                    raise
                await asyncio.sleep(base_delay * attempt)
        if last_error:
            raise last_error

    def _is_invalid_replies_error(self, error: Exception) -> bool:
        name = error.__class__.__name__
        text = str(error).lower()
        return name in {"MsgIdInvalidError", "MessageIdInvalidError"} or (
            "getrepliesrequest" in text and "message id" in text and "invalid" in text
        )

    def _create_adaptive_delay_state(self, base_delay: int) -> dict[str, int | bool]:
        normalized_base_delay = max(int(base_delay), 1)
        normalized_max_delay = max(ADAPTIVE_DELAY_MAX_SECONDS, normalized_base_delay)
        return {
            "base_delay": normalized_base_delay,
            "current_delay": normalized_base_delay,
            "max_delay": normalized_max_delay,
            "success_streak": 0,
            "floodwait_triggered": False,
            "max_applied_delay": normalized_base_delay,
        }

    def _register_successful_send(self, adaptive_delay: dict[str, int | bool]) -> None:
        current_delay = int(adaptive_delay["current_delay"])
        base_delay = int(adaptive_delay["base_delay"])
        if current_delay <= base_delay:
            adaptive_delay["success_streak"] = 0
            return

        adaptive_delay["success_streak"] = int(adaptive_delay["success_streak"]) + 1
        if int(adaptive_delay["success_streak"]) < max(ADAPTIVE_DELAY_DECAY_EVERY_SUCCESS, 1):
            return

        adaptive_delay["current_delay"] = max(base_delay, current_delay - max(ADAPTIVE_DELAY_STEP_SECONDS, 1))
        adaptive_delay["success_streak"] = 0

    def _register_floodwait(
        self,
        adaptive_delay: dict[str, int | bool],
        wait_seconds: int,
    ) -> int:
        base_delay = int(adaptive_delay["base_delay"])
        current_delay = int(adaptive_delay["current_delay"])
        max_delay = int(adaptive_delay["max_delay"])
        divisor = max(FLOODWAIT_DELAY_SCALE_DIVISOR, 1)
        scaled_boost = math.ceil(wait_seconds / divisor)
        target_delay = base_delay + max(FLOODWAIT_EXTRA_DELAY_SECONDS, 0) + scaled_boost
        next_delay = min(max_delay, max(current_delay + max(ADAPTIVE_DELAY_STEP_SECONDS, 1), target_delay))

        adaptive_delay["current_delay"] = next_delay
        adaptive_delay["success_streak"] = 0
        adaptive_delay["floodwait_triggered"] = True
        adaptive_delay["max_applied_delay"] = max(int(adaptive_delay["max_applied_delay"]), next_delay)
        return next_delay

    async def _sleep_with_adaptive_delay(self, adaptive_delay: dict[str, int | bool]) -> None:
        base_sleep = max(int(adaptive_delay["current_delay"]), 0)
        jitter = 0
        if ADAPTIVE_DELAY_JITTER_SECONDS > 0:
            jitter = random.randint(0, ADAPTIVE_DELAY_JITTER_SECONDS)
        await asyncio.sleep(base_sleep + jitter)

    def _mailing_error_reason(self, error: Exception) -> str:
        name = error.__class__.__name__
        text = str(error).lower()

        if name in {"UsernameNotOccupiedError", "UsernameInvalidError"}:
            return "username не существует или указан неверно"
        if name in {"UserPrivacyRestrictedError", "PrivacyError"}:
            return "получатель ограничил личные сообщения настройками приватности"
        if name in {"PeerFloodError"}:
            return "Telegram ограничил отправку из-за высокой активности"
        if name in {"UserIsBlockedError"}:
            return "пользователь заблокировал этот аккаунт"
        if name in {"InputUserDeactivatedError"}:
            return "аккаунт получателя деактивирован"
        if name in {"ChatWriteForbiddenError"}:
            return "нет прав на отправку сообщений этому получателю"
        if "resolveusernamerequest" in text:
            return "слишком частая проверка username, Telegram временно ограничил запросы"
        return f"непредвиденная ошибка Telegram API: {error}"

    def create_mailing_control(self, owner_user_id: int, chat_id: int) -> str:
        self._mailing_control_seq += 1
        control_id = str(self._mailing_control_seq)
        self._mailing_controls[control_id] = {
            "owner_user_id": owner_user_id,
            "chat_id": chat_id,
            "paused": False,
            "active": True,
        }
        return control_id

    def get_mailing_control(self, control_id: str) -> dict | None:
        return self._mailing_controls.get(control_id)

    def set_mailing_paused(self, control_id: str, paused: bool) -> bool:
        control = self._mailing_controls.get(control_id)
        if not control or not control.get("active"):
            return False
        control["paused"] = paused
        return True

    def finish_mailing_control(self, control_id: str) -> None:
        control = self._mailing_controls.get(control_id)
        if control:
            control["active"] = False

    async def _wait_if_paused(self, control_id: str | None) -> None:
        if not control_id:
            return
        while True:
            control = self._mailing_controls.get(control_id)
            if not control or not control.get("active"):
                return
            if not control.get("paused", False):
                return
            await asyncio.sleep(0.5)

    async def _safe_edit_text(
        self,
        message,
        text: str,
        *,
        parse_mode: str | None = None,
        last_text: str | None = None,
    ) -> str | None:
        if not message:
            return last_text
        if text == last_text:
            return last_text
        try:
            await message.edit_text(text, parse_mode=parse_mode)
            return text
        except Exception as e:
            # aiogram может вернуть BadRequest, если контент фактически не изменился.
            error_text = str(e).lower()
            error_name = e.__class__.__name__.lower()
            if "message is not modified" in error_text:
                return last_text
            # Временные сетевые проблемы Bot API не должны прерывать основной процесс рассылки.
            if self._is_timeout_error(e) or "telegramnetworkerror" in error_name:
                print(f"Временная ошибка обновления прогресса: {e}")
                return last_text
            raise

    async def send_bulk(
        self,
        session,
        usernames,
        text,
        delay,
        bot=None,
        chat_id=None,
        parse_mode: str | None = None,
        control_id: str | None = None,
        max_floodwait_seconds: int = MAX_FLOODWAIT_SECONDS,
    ):
        client = await self.get_client(session)
        await client.connect()

        total = len(usernames)
        sent = 0
        failed = []
        successful_usernames = []
        floodwait_events = 0
        max_floodwait_seen = 0
        adaptive_delay = self._create_adaptive_delay_state(delay)

        progress_message = None
        last_progress_text = None
        rate_limit_notified = False

        # если передали bot - показываем прогресс
        if bot and chat_id:
            progress_message = await bot.send_message(chat_id, "🚀 Запуск рассылки...")

        for username in usernames:
            await self._wait_if_paused(control_id)
            while True:
                try:
                    await client.send_message(username, text, parse_mode=parse_mode)
                    sent += 1
                    successful_usernames.append(username)
                    self._register_successful_send(adaptive_delay)
                    break
                except FloodWaitError as e:
                    wait_seconds = max(int(getattr(e, "seconds", 0)), 1)
                    floodwait_events += 1
                    if wait_seconds > max_floodwait_seen:
                        max_floodwait_seen = wait_seconds
                    next_delay = self._register_floodwait(adaptive_delay, wait_seconds)
                    if wait_seconds > max_floodwait_seconds:
                        if bot and chat_id and not rate_limit_notified:
                            try:
                                await bot.send_message(
                                    chat_id,
                                    (
                                        "⏳ Обнаружен длительный FloodWait от Telegram. "
                                        "Это ограничение аккаунта, а не ошибка бота. "
                                        "Проблемные username будут пропущены."
                                    ),
                                )
                            except Exception as notify_error:
                                print(f"Не удалось отправить уведомление о FloodWait: {notify_error}")
                            rate_limit_notified = True
                        print(
                            f"FloodWait при отправке {username}: {wait_seconds} сек > лимита "
                            f"{max_floodwait_seconds} сек, пропускаю. "
                            f"Новая адаптивная задержка: {next_delay} сек."
                        )
                        failed.append(
                            {
                                "username": username,
                                "error_name": e.__class__.__name__,
                                "reason": (
                                    f"пропущено: FloodWait {wait_seconds} сек "
                                    f"(лимит {max_floodwait_seconds} сек)"
                                ),
                            }
                        )
                        break
                    print(
                        f"FloodWait при отправке {username}: жду {wait_seconds} сек. "
                        f"Следующая базовая задержка повышена до {next_delay} сек."
                    )
                    await asyncio.sleep(wait_seconds)
                except Exception as e:
                    print(f"Ошибка при отправке {username}: {e}")
                    failed.append(
                        {
                            "username": username,
                            "error_name": e.__class__.__name__,
                            "reason": self._mailing_error_reason(e),
                        }
                    )
                    break

            if progress_message:
                progress = generate_progress(sent, total)
                progress_text = f"💎 <b>Рассылка в процессе...</b>\n\n{progress}"
                last_progress_text = await self._safe_edit_text(
                    progress_message,
                    progress_text,
                    parse_mode="HTML",
                    last_text=last_progress_text,
                )

            await self._sleep_with_adaptive_delay(adaptive_delay)

        if progress_message:
            done_text = (
                f"✅ <b>Рассылка завершена</b>\n\n"
                f"📤 Отправлено: {sent}\n"
                f"👥 Всего: {total}\n"
                f"❌ Ошибок: {len(failed)}"
            )
            await self._safe_edit_text(
                progress_message,
                done_text,
                parse_mode="HTML",
                last_text=last_progress_text,
            )

        await client.disconnect()
        return {
            "sent": sent,
            "total": total,
            "successful_usernames": successful_usernames,
            "failed": failed,
            "rate_limit": {
                "detected": floodwait_events > 0,
                "events": floodwait_events,
                "max_wait_seconds": max_floodwait_seen,
                "likely_account_limit": max_floodwait_seen >= max_floodwait_seconds,
                "adaptive_delay_applied": bool(adaptive_delay["floodwait_triggered"]),
                "recommended_delay_seconds": int(adaptive_delay["current_delay"]),
                "max_applied_delay_seconds": int(adaptive_delay["max_applied_delay"]),
            },
        }

    async def save_bulk_drafts(
        self,
        session,
        usernames,
        text,
        delay,
        bot=None,
        chat_id=None,
        parse_mode: str | None = None,
        control_id: str | None = None,
        max_floodwait_seconds: int = MAX_FLOODWAIT_SECONDS,
    ):
        client = await self.get_client(session)
        await client.connect()

        total = len(usernames)
        saved = 0
        failed = []
        successful_usernames = []
        floodwait_events = 0
        max_floodwait_seen = 0
        adaptive_delay = self._create_adaptive_delay_state(delay)

        progress_message = None
        last_progress_text = None
        rate_limit_notified = False

        if bot and chat_id:
            progress_message = await bot.send_message(chat_id, "📝 Сохраняю черновики...")

        for username in usernames:
            await self._wait_if_paused(control_id)
            while True:
                try:
                    peer = await client.get_input_entity(username)
                    draft_text = text
                    draft_entities = None
                    if parse_mode:
                        parser = sanitize_parse_mode(parse_mode)
                        draft_text, draft_entities = parser.parse(text)
                    await client(
                        SaveDraftRequest(
                            peer=peer,
                            message=draft_text,
                            no_webpage=False,
                            reply_to=None,
                            entities=draft_entities,
                        )
                    )
                    saved += 1
                    successful_usernames.append(username)
                    self._register_successful_send(adaptive_delay)
                    break
                except FloodWaitError as e:
                    wait_seconds = max(int(getattr(e, "seconds", 0)), 1)
                    floodwait_events += 1
                    if wait_seconds > max_floodwait_seen:
                        max_floodwait_seen = wait_seconds
                    next_delay = self._register_floodwait(adaptive_delay, wait_seconds)
                    if wait_seconds > max_floodwait_seconds:
                        if bot and chat_id and not rate_limit_notified:
                            try:
                                await bot.send_message(
                                    chat_id,
                                    (
                                        "⏳ Обнаружен длительный FloodWait от Telegram. "
                                        "Это ограничение аккаунта, а не ошибка бота. "
                                        "Проблемные username будут пропущены."
                                    ),
                                )
                            except Exception as notify_error:
                                print(f"Не удалось отправить уведомление о FloodWait: {notify_error}")
                            rate_limit_notified = True
                        print(
                            f"FloodWait при сохранении черновика {username}: {wait_seconds} сек > лимита "
                            f"{max_floodwait_seconds} сек, пропускаю. "
                            f"Новая адаптивная задержка: {next_delay} сек."
                        )
                        failed.append(
                            {
                                "username": username,
                                "error_name": e.__class__.__name__,
                                "reason": (
                                    f"пропущено: FloodWait {wait_seconds} сек "
                                    f"(лимит {max_floodwait_seconds} сек)"
                                ),
                            }
                        )
                        break
                    print(
                        f"FloodWait при сохранении черновика {username}: жду {wait_seconds} сек. "
                        f"Следующая базовая задержка повышена до {next_delay} сек."
                    )
                    await asyncio.sleep(wait_seconds)
                except Exception as e:
                    print(f"Ошибка при сохранении черновика {username}: {e}")
                    failed.append(
                        {
                            "username": username,
                            "error_name": e.__class__.__name__,
                            "reason": self._mailing_error_reason(e),
                        }
                    )
                    break

            if progress_message:
                progress = generate_progress(saved, total)
                progress_text = f"📝 <b>Сохранение черновиков...</b>\n\n{progress}"
                last_progress_text = await self._safe_edit_text(
                    progress_message,
                    progress_text,
                    parse_mode="HTML",
                    last_text=last_progress_text,
                )

            await self._sleep_with_adaptive_delay(adaptive_delay)

        if progress_message:
            done_text = (
                f"✅ <b>Сохранение черновиков завершено</b>\n\n"
                f"📝 Сохранено: {saved}\n"
                f"👥 Всего: {total}\n"
                f"❌ Ошибок: {len(failed)}"
            )
            await self._safe_edit_text(
                progress_message,
                done_text,
                parse_mode="HTML",
                last_text=last_progress_text,
            )

        await client.disconnect()
        return {
            "sent": saved,
            "total": total,
            "successful_usernames": successful_usernames,
            "failed": failed,
            "rate_limit": {
                "detected": floodwait_events > 0,
                "events": floodwait_events,
                "max_wait_seconds": max_floodwait_seen,
                "likely_account_limit": max_floodwait_seen >= max_floodwait_seconds,
                "adaptive_delay_applied": bool(adaptive_delay["floodwait_triggered"]),
                "recommended_delay_seconds": int(adaptive_delay["current_delay"]),
                "max_applied_delay_seconds": int(adaptive_delay["max_applied_delay"]),
            },
        }

    async def scan_channel_comment_usernames(
        self,
        session: str,
        channel_ref: str,
        post_limit: int | None = None,
        progress_callback=None,
        progress_step: int = 10,
    ):
        client = await self.get_client(session)
        await client.connect()

        usernames_map = {}
        scanned_count = 0
        sender_cache: dict[int, str | None] = {}
        resolve_semaphore = asyncio.Semaphore(self._scan_sender_resolve_limit)

        try:
            entity = await client.get_entity(channel_ref)
            is_channel = isinstance(entity, Channel) and not getattr(entity, "megagroup", False)
            scan_kind = "posts" if is_channel else "messages"

            if is_channel:
                async for post in client.iter_messages(entity):
                    # Для канала берём только посты, а не сервисные сообщения.
                    if not getattr(post, "post", False):
                        continue

                    scanned_count += 1

                    # Если комментариев нет, не дергаем replies endpoint лишний раз.
                    replies_meta = getattr(post, "replies", None)
                    has_replies = bool(replies_meta and getattr(replies_meta, "replies", 0) > 0)
                    if not has_replies:
                        if post_limit is not None and post_limit > 0 and scanned_count >= post_limit:
                            break
                        continue

                    comment_chunk = []
                    while True:
                        try:
                            async for comment in client.iter_messages(entity, reply_to=post.id):
                                comment_chunk.append(comment)
                                if len(comment_chunk) >= self._scan_chunk_size:
                                    await self._flush_scan_chunk(
                                        comment_chunk,
                                        usernames_map,
                                        sender_cache,
                                        resolve_semaphore,
                                    )
                                    comment_chunk.clear()
                            await self._flush_scan_chunk(
                                comment_chunk,
                                usernames_map,
                                sender_cache,
                                resolve_semaphore,
                            )
                            break
                        except FloodWaitError as e:
                            await self._flush_scan_chunk(
                                comment_chunk,
                                usernames_map,
                                sender_cache,
                                resolve_semaphore,
                            )
                            comment_chunk.clear()
                            await asyncio.sleep(e.seconds)
                        except Exception as e:
                            # Некоторые посты имеют невалидный/недоступный thread комментариев.
                            await self._flush_scan_chunk(
                                comment_chunk,
                                usernames_map,
                                sender_cache,
                                resolve_semaphore,
                            )
                            comment_chunk.clear()
                            if self._is_invalid_replies_error(e):
                                break
                            raise

                    if progress_callback and progress_step > 0 and scanned_count % progress_step == 0:
                        await progress_callback(scanned_count, len(usernames_map), scan_kind)

                    if post_limit is not None and post_limit > 0 and scanned_count >= post_limit:
                        break
            else:
                message_chunk = []
                async for message in client.iter_messages(entity):
                    scanned_count += 1
                    message_chunk.append(message)
                    if len(message_chunk) >= self._scan_chunk_size:
                        await self._flush_scan_chunk(
                            message_chunk,
                            usernames_map,
                            sender_cache,
                            resolve_semaphore,
                        )
                        message_chunk.clear()

                    if progress_callback and progress_step > 0 and scanned_count % progress_step == 0:
                        await progress_callback(scanned_count, len(usernames_map), scan_kind)

                    if post_limit is not None and post_limit > 0 and scanned_count >= post_limit:
                        break

                await self._flush_scan_chunk(
                    message_chunk,
                    usernames_map,
                    sender_cache,
                    resolve_semaphore,
                )

            if progress_callback:
                await progress_callback(scanned_count, len(usernames_map), scan_kind)

            usernames = sorted(usernames_map.values(), key=lambda x: x.lower())
            return usernames, scanned_count, scan_kind
        finally:
            await client.disconnect()

    async def check_usernames_channel_status(
        self,
        session: str,
        usernames: list[str],
        progress_callback=None,
        wait_callback=None,
        progress_step: int = 10,
        max_floodwait_seconds: int = 300,
    ):
        client = await self.get_client(session)
        await self._connect_with_retry(client)

        results = []
        checked = 0

        try:
            for raw_username in usernames:
                username = raw_username.replace("@", "").strip()
                if not username:
                    continue

                timeout_attempt = 0
                while True:
                    try:
                        entity = await client.get_entity(username)

                        if isinstance(entity, Channel) or isinstance(entity, Chat):
                            results.append(
                                {
                                    "username": username,
                                    "has_channel": True,
                                    "status": "да (username принадлежит каналу/чату)",
                                    "channel_visibility": "open",
                                }
                            )
                            checked += 1
                            break

                        if isinstance(entity, User):
                            input_user = await client.get_input_entity(entity)
                            full = await client(GetFullUserRequest(input_user))
                            full_user = getattr(full, "full_user", None)
                            personal_channel_id = getattr(full_user, "personal_channel_id", None)
                            has_channel = bool(personal_channel_id)
                            channel_visibility = None
                            if has_channel:
                                try:
                                    ch_entity = await client.get_entity(PeerChannel(personal_channel_id))
                                    channel_visibility = (
                                        "open" if getattr(ch_entity, "username", None) else "closed"
                                    )
                                except Exception:
                                    channel_visibility = None
                            results.append(
                                {
                                    "username": username,
                                    "has_channel": has_channel,
                                    "status": "да" if has_channel else "нет",
                                    "channel_visibility": channel_visibility,
                                }
                            )
                            checked += 1
                            break

                        results.append(
                            {
                                "username": username,
                                "has_channel": None,
                                "status": "не удалось определить тип аккаунта",
                                "channel_visibility": None,
                            }
                        )
                        checked += 1
                        break
                    except FloodWaitError as e:
                        wait_seconds = max(int(getattr(e, "seconds", 0)), 1)
                        if wait_seconds > max_floodwait_seconds:
                            results.append(
                                {
                                    "username": username,
                                    "has_channel": None,
                                    "status": f"пропущен: FloodWait {wait_seconds} сек",
                                }
                            )
                            checked += 1
                            if wait_callback:
                                await wait_callback(
                                    checked,
                                    len(usernames),
                                    username,
                                    wait_seconds,
                                    "floodwait_skip",
                                )
                            break
                        if wait_callback:
                            await wait_callback(checked, len(usernames), username, wait_seconds, "floodwait")
                        await asyncio.sleep(wait_seconds)
                    except Exception as e:
                        if self._is_timeout_error(e) and timeout_attempt < 3:
                            timeout_attempt += 1
                            if wait_callback:
                                await wait_callback(
                                    checked,
                                    len(usernames),
                                    username,
                                    timeout_attempt,
                                    "reconnect",
                                )
                            try:
                                await client.disconnect()
                            except Exception:
                                pass
                            await asyncio.sleep(timeout_attempt)
                            await self._connect_with_retry(client)
                            continue
                        results.append(
                            {
                                "username": username,
                                "has_channel": None,
                                "status": f"ошибка: {e.__class__.__name__}",
                                "channel_visibility": None,
                            }
                        )
                        checked += 1
                        break

                if progress_callback and progress_step > 0 and checked % progress_step == 0:
                    await progress_callback(checked, len(usernames))

            if progress_callback:
                await progress_callback(checked, len(usernames))

            return results
        finally:
            await client.disconnect()
