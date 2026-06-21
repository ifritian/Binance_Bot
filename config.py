"""
Все настройки бота читаются из .env файла (см. .env.example).
Никаких ключей в коде - только через переменные окружения.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent

# --- Telegram (публичный канал - скрапим preview-страницу, без авторизации) ---
# Username канала без @, например: resultrsi
FOLLOWUP_CHANNEL_USERNAME = os.environ.get("FOLLOWUP_CHANNEL_USERNAME", "")
TELEGRAM_PREVIEW_URL = "https://t.me/s/{channel}"

# --- Binance Square ---
BINANCE_SQUARE_API_KEY = os.environ.get("BINANCE_SQUARE_API_KEY", "")
BINANCE_SQUARE_BASE_V1 = "https://www.binance.com/bapi/composite/v1/public/pgc/openApi"
BINANCE_SQUARE_BASE_V2 = "https://www.binance.com/bapi/composite/v2/public/pgc/openApi"
BINANCE_SQUARE_ENDPOINT = f"{BINANCE_SQUARE_BASE_V1}/content/add"

# --- Groq (генерация текста) ---
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_VISION_MODEL = os.environ.get("GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")

# --- Поведение бота ---
MIN_POST_INTERVAL_HOURS = float(os.environ.get("MIN_POST_INTERVAL_HOURS", "4"))          # валюта
OPINION_INTERVAL_HOURS = float(os.environ.get("OPINION_INTERVAL_HOURS", "48"))            # личное мнение - раз в 2 дня
ARTICLE_INTERVAL_HOURS = float(os.environ.get("ARTICLE_INTERVAL_HOURS", "168"))            # статья - раз в неделю
POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "60"))

DB_PATH = BASE_DIR / "bot_state.db"
LOG_PATH = BASE_DIR / "bot.log"


def validate_config() -> list[str]:
    """Возвращает список незаполненных обязательных переменных."""
    required = {
        "FOLLOWUP_CHANNEL_USERNAME": FOLLOWUP_CHANNEL_USERNAME,
        "BINANCE_SQUARE_API_KEY": BINANCE_SQUARE_API_KEY,
        "GROQ_API_KEY": GROQ_API_KEY,
    }
    return [name for name, value in required.items() if not value]