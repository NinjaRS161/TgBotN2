from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cryptography.fernet import Fernet, InvalidToken
else:
    try:
        from cryptography.fernet import Fernet, InvalidToken
    except Exception:  # pragma: no cover - fallback when dependency is missing
        Fernet = None
        InvalidToken = Exception


_SESSION_PREFIX = "enc:"


def _load_key() -> str | None:
    key = os.getenv("SESSION_ENC_KEY")
    if not key:
        raise RuntimeError(
            "SESSION_ENC_KEY не задан. Для безопасности хранения сессий "
            "задайте ключ Fernet в переменной окружения SESSION_ENC_KEY."
        )
    return key.strip()


def _build_cipher() -> Fernet | None:
    key = _load_key()
    if not key:
        return None
    if Fernet is None:
        raise RuntimeError(
            "SESSION_ENC_KEY задан, но пакет cryptography не установлен. "
            "Установите cryptography или удалите SESSION_ENC_KEY."
        )
    try:
        return Fernet(key.encode("utf-8"))
    except Exception as exc:
        raise ValueError("SESSION_ENC_KEY имеет неверный формат для Fernet.") from exc


_CIPHER = _build_cipher()


def session_encryption_active() -> bool:
    return _CIPHER is not None


def encrypt_session(session: str | None) -> str | None:
    if not session:
        return session
    if session.startswith(_SESSION_PREFIX):
        return session
    if _CIPHER is None:
        return session
    token = _CIPHER.encrypt(session.encode("utf-8")).decode("utf-8")
    return f"{_SESSION_PREFIX}{token}"


def decrypt_session(value: str | None) -> str | None:
    if value is None:
        return None
    if not value.startswith(_SESSION_PREFIX):
        return value
    if _CIPHER is None:
        return None
    token = value[len(_SESSION_PREFIX) :]
    try:
        return _CIPHER.decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        return None
