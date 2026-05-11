import json
import os
from collections.abc import Mapping
from typing import Any

import aiosqlite
from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey


class SQLiteStorage(BaseStorage):
    def __init__(self, db_path: str):
        self.db_path = os.path.abspath(db_path)
        self._initialized = False

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS fsm_storage (
                    bot_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    thread_id INTEGER NOT NULL DEFAULT 0,
                    business_connection_id TEXT NOT NULL DEFAULT '',
                    destiny TEXT NOT NULL DEFAULT 'default',
                    state TEXT,
                    data TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (
                        bot_id,
                        chat_id,
                        user_id,
                        thread_id,
                        business_connection_id,
                        destiny
                    )
                )
                """
            )
            await db.commit()

        self._initialized = True

    @staticmethod
    def _serialize_state(state: StateType = None) -> str | None:
        if state is None:
            return None
        if isinstance(state, State):
            return state.state
        return str(state)

    @staticmethod
    def _normalize_data(data: Mapping[str, Any] | None) -> dict[str, Any]:
        if not data:
            return {}
        return dict(data)

    @staticmethod
    def _row_values(key: StorageKey) -> tuple[Any, ...]:
        return (
            key.bot_id,
            key.chat_id,
            key.user_id,
            key.thread_id or 0,
            key.business_connection_id or "",
            key.destiny,
        )

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        await self._ensure_initialized()
        state_value = self._serialize_state(state)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO fsm_storage (
                    bot_id, chat_id, user_id, thread_id, business_connection_id, destiny, state
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(bot_id, chat_id, user_id, thread_id, business_connection_id, destiny)
                DO UPDATE SET
                    state=excluded.state,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (*self._row_values(key), state_value),
            )
            await db.commit()

    async def get_state(self, key: StorageKey) -> str | None:
        await self._ensure_initialized()

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT state
                FROM fsm_storage
                WHERE bot_id = ? AND chat_id = ? AND user_id = ? AND thread_id = ?
                  AND business_connection_id = ? AND destiny = ?
                """,
                self._row_values(key),
            )
            row = await cursor.fetchone()
            return row[0] if row else None

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        await self._ensure_initialized()
        payload = json.dumps(self._normalize_data(data), ensure_ascii=False)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO fsm_storage (
                    bot_id, chat_id, user_id, thread_id, business_connection_id, destiny, data
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(bot_id, chat_id, user_id, thread_id, business_connection_id, destiny)
                DO UPDATE SET
                    data=excluded.data,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (*self._row_values(key), payload),
            )
            await db.commit()

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        await self._ensure_initialized()

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT data
                FROM fsm_storage
                WHERE bot_id = ? AND chat_id = ? AND user_id = ? AND thread_id = ?
                  AND business_connection_id = ? AND destiny = ?
                """,
                self._row_values(key),
            )
            row = await cursor.fetchone()
            if not row or not row[0]:
                return {}
            return json.loads(row[0])

    async def close(self) -> None:
        self._initialized = False
