import csv
import io
import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from telegram import InputFile, Update
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

# If a Railway Volume is attached, RAILWAY_VOLUME_MOUNT_PATH is set automatically and the
# database is stored there so it survives redeploys. Otherwise it falls back to a local file
# (fine locally, but resets on every Railway redeploy without a volume).
DB_PATH = os.environ.get("DB_PATH") or os.path.join(
    os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "."), "users.db"
)

# Comma-separated Telegram user IDs allowed to use /stats and /export
ADMIN_IDS = {
    int(x.strip()) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip().isdigit()
}

MESSAGE_TEXT = (
    "Треба допомагати хлопцям на передку. Будь-який донат вже важливий — "
    "хлопці розуміють, що за ними підтримка, що про них пам'ятають, що вони не самі.\n\n"
    f"🙏 Будь-який донат від 100 грн: {PAYMENT_LINK}\n\n"
    "Після оплати, будь ласка, надішліть сюди скріншот."
)

THANK_YOU_TEXT = (
    "Дякуємо за оплату! 🙏 Скріншот отримано.\n\n"
    "Завтра вам надійде гайд по роботі з мисленням, як ефективно змінювати своє життя."
)

MATERIAL_LINK = os.environ.get(
    "MATERIAL_LINK",
    "https://app.notion.com/p/3616c400a058806ab9f6edc9d7761e2d?source=copy_link",
)
MATERIAL_TEXT = (
    "🧠 Як обіцяли — гайд по роботі з мисленням, як ефективно змінювати своє життя:\n"
    f"{MATERIAL_LINK}"
)
# How long after a payment screenshot to deliver the material
MATERIAL_DELAY = timedelta(hours=24)
# How often to check for materials that are due to be sent (seconds)
MATERIAL_CHECK_INTERVAL = 600


def build_keyword_pattern(keywords: list[str]) -> re.Pattern:
    """Builds a single case-insensitive regex that matches any of the keywords
    as a substring anywhere in the message (covers word forms like 'донат', 'задонатити')."""
    escaped = [re.escape(k) for k in keywords]
    pattern = "|".join(escaped)
    return re.compile(pattern, re.IGNORECASE)


KEYWORD_PATTERN = build_keyword_pattern(KEYWORDS)


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            first_seen TEXT,
            last_seen TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pending_materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            send_at TEXT NOT NULL,
            sent INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.commit()
    conn.close()


def schedule_material(user_id: int) -> None:
    send_at = (datetime.now(timezone.utc) + MATERIAL_DELAY).isoformat()
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO pending_materials (user_id, send_at, sent) VALUES (?, ?, 0)",
        (user_id, send_at),
    )
    conn.commit()
    conn.close()


async def deliver_pending_materials(context) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT id, user_id FROM pending_materials WHERE sent = 0 AND send_at <= ?",
        (now,),
    ).fetchall()

    for row_id, user_id in rows:
        try:
            await context.bot.send_message(chat_id=user_id, text=MATERIAL_TEXT)
            conn.execute("UPDATE pending_materials SET sent = 1 WHERE id = ?", (row_id,))
            conn.commit()
        except Exception:
            logger.exception("Failed to deliver material to user %s", user_id)

    conn.close()


def save_user(user) -> None:
    if user is None:
        return
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        INSERT INTO users (id, username, first_name, last_name, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            username = excluded.username,
            first_name = excluded.first_name,
            last_name = excluded.last_name,
            last_seen = excluded.last_seen
        """,
        (user.id, user.username, user.first_name, user.last_name, now, now),
    )
    conn.commit()
    conn.close()


async def send_payment_message(update: Update) -> None:
    save_user(update.effective_user)

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


def format_payment_caption(user) -> str:
    name = " ".join(filter(None, [getattr(user, "first_name", None), getattr(user, "last_name", None)])).strip()
    username = f"@{user.username}" if getattr(user, "username", None) else "немає username"
    return (
        "💸 Новий скріншот оплати!\n\n"
        f"Ім'я: {name or '—'}\n"
        f"Username: {username}\n"
        f"Telegram ID: {user.id}"
    )


async def payment_screenshot(update: Update, context) -> None:
    user = update.effective_user
    save_user(user)

    # Thank the person who sent the screenshot and queue tomorrow's material
    await update.message.reply_text(THANK_YOU_TEXT)
    schedule_material(user.id)

    # Notify admin(s) privately with the same screenshot
    if not ADMIN_IDS:
        logger.warning("ADMIN_IDS is not set — no one was notified about this payment screenshot")
        return

    photo_file_id = update.message.photo[-1].file_id
    caption = format_payment_caption(user)
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_photo(chat_id=admin_id, photo=photo_file_id, caption=caption)
        except Exception:
            logger.exception("Failed to notify admin %s about payment screenshot", admin_id)


def is_admin(update: Update) -> bool:
    return bool(update.effective_user) and update.effective_user.id in ADMIN_IDS


async def stats(update: Update, context) -> None:
    if not is_admin(update):
        return
    conn = sqlite3.connect(DB_PATH)
    count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()
    await update.message.reply_text(f"Користувачів у базі: {count}")


async def export_users(update: Update, context) -> None:
    if not is_admin(update):
        return
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT id, username, first_name, last_name, first_seen, last_seen FROM users ORDER BY first_seen"
    ).fetchall()
    conn.close()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "username", "first_name", "last_name", "first_seen", "last_seen"])
    writer.writerows(rows)
    data = buf.getvalue().encode("utf-8")

    await update.message.reply_document(
        document=InputFile(io.BytesIO(data), filename="users.csv")
    )


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN env var is not set")

    init_db()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("export", export_users))
    application.add_handler(
        MessageHandler(filters.TEXT & filters.Regex(KEYWORD_PATTERN), keyword_trigger)
    )
    application.add_handler(MessageHandler(filters.PHOTO, payment_screenshot))

    application.job_queue.run_repeating(
        deliver_pending_materials, interval=MATERIAL_CHECK_INTERVAL, first=10
    )

    logger.info("Bot started. Keywords: %s", KEYWORDS)
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
