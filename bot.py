import logging
import os
import re

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

load_dotenv()  # reads .env locally; no-op on Railway (uses dashboard Variables)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
PAYMENT_LINK = os.environ.get("PAYMENT_LINK", "https://www.privat24.ua/send/jxvly")
# Comma-separated list of trigger keywords, e.g. "донат,задонатити,підтримати"
KEYWORDS = [w.strip().lower() for w in os.environ.get("KEYWORDS", "донат").split(",") if w.strip()]

PHOTO_FILENAME = os.environ.get("PHOTO_FILENAME", "IMG_9933.jpg")
PHOTO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), PHOTO_FILENAME)

MESSAGE_TEXT = (
    "Дякую за підтримку! 🙏\n\n"
    f"Посилання для оплати: {PAYMENT_LINK}"
)


def build_keyword_pattern(keywords: list[str]) -> re.Pattern:
    """Builds a single case-insensitive regex that matches any of the keywords
    as a substring anywhere in the message (covers word forms like 'донат', 'задонатити')."""
    escaped = [re.escape(k) for k in keywords]
    pattern = "|".join(escaped)
    return re.compile(pattern, re.IGNORECASE)


KEYWORD_PATTERN = build_keyword_pattern(KEYWORDS)


async def send_payment_message(update: Update) -> None:
    if os.path.isfile(PHOTO_PATH):
        with open(PHOTO_PATH, "rb") as photo:
            await update.message.reply_photo(photo=photo, caption=MESSAGE_TEXT)
    else:
        logger.warning("Photo not found at %s, sending text only", PHOTO_PATH)
        await update.message.reply_text(MESSAGE_TEXT)


async def start(update: Update, context) -> None:
    await send_payment_message(update)


async def keyword_trigger(update: Update, context) -> None:
    await send_payment_message(update)


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN env var is not set")

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(
        MessageHandler(filters.TEXT & filters.Regex(KEYWORD_PATTERN), keyword_trigger)
    )

    logger.info("Bot started. Keywords: %s", KEYWORDS)
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
