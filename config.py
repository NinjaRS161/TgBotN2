import os
from dotenv import load_dotenv

# Загружаем переменные из .env
load_dotenv()

BASE_DIR = os.path.dirname(__file__)

def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Переменная окружения {name} обязательна и не задана.")
    return value


def _require_int(name: str) -> int:
    raw = _require_env(name)
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"Переменная {name} должна быть целым числом.") from exc


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"Переменная {name} должна быть целым числом.") from exc


# ====== Telegram Bot ======
BOT_TOKEN = _require_env("BOT_TOKEN")

# ====== Telegram API для Userbot ======
API_ID = _require_int("API_ID")
API_HASH = _require_env("API_HASH")

# ====== Доступ и оплата ======
SUB_PRICE = int(os.getenv("SUB_PRICE", 999))

# ====== Рассылка ======
MIN_DELAY = int(os.getenv("MIN_DELAY", 5))
MAX_FLOODWAIT_SECONDS = int(os.getenv("MAX_FLOODWAIT_SECONDS", 300))
ADAPTIVE_DELAY_MAX_SECONDS = _get_int("ADAPTIVE_DELAY_MAX_SECONDS", 120)
ADAPTIVE_DELAY_STEP_SECONDS = _get_int("ADAPTIVE_DELAY_STEP_SECONDS", 3)
ADAPTIVE_DELAY_DECAY_EVERY_SUCCESS = _get_int("ADAPTIVE_DELAY_DECAY_EVERY_SUCCESS", 4)
ADAPTIVE_DELAY_JITTER_SECONDS = _get_int("ADAPTIVE_DELAY_JITTER_SECONDS", 1)
FLOODWAIT_EXTRA_DELAY_SECONDS = _get_int("FLOODWAIT_EXTRA_DELAY_SECONDS", 5)
FLOODWAIT_DELAY_SCALE_DIVISOR = _get_int("FLOODWAIT_DELAY_SCALE_DIVISOR", 10)
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
POLLING_RETRY_BASE_SECONDS = int(os.getenv("POLLING_RETRY_BASE_SECONDS", 3))
POLLING_RETRY_MAX_SECONDS = int(os.getenv("POLLING_RETRY_MAX_SECONDS", 60))
BOT_DATA_DIR = os.getenv("BOT_DATA_DIR", BASE_DIR)
DATABASE_PATH = os.getenv("DATABASE_PATH", os.path.join(BOT_DATA_DIR, "database.db"))

# ====== Админ ======
ADMIN_ID = _require_int("ADMIN_ID")

# ====== Шифрование сессий ======
SESSION_ENC_KEY = _require_env("SESSION_ENC_KEY")
