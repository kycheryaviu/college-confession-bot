import os
import asyncio
import sqlite3
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.exceptions import TelegramBadRequest
logging.basicConfig(level=logging.INFO)
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_GROUP_ID = int(os.getenv("ADMIN_GROUP_ID", "-1004412864686"))
CHANNEL_ID = os.getenv("CHANNEL_ID", "@okotsitomsk")
DB_FILE = "bot.db"
FOOTER = "Отправлено с бота @Anon_okotsit_bot"
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
def connect_db():
    return sqlite3.connect(DB_FILE)
def init_db():
    conn = connect_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            user_message_id INTEGER,
            source_message_id INTEGER,
            status TEXT DEFAULT 'pending',
            admin_message_id INTEGER
        )
    """)
    cur.execute("PRAGMA table_info(submissions)")
    columns = {row[1] for row in cur.fetchall()}
    additions = {
        "user_id": "INTEGER",
        "user_message_id": "INTEGER",
        "source_message_id": "INTEGER",
        "status": "TEXT DEFAULT 'pending'",
        "admin_message_id": "INTEGER",
    }
    for name, definition in additions.items():
        if name not in columns:
            cur.execute(
                f"ALTER TABLE submissions ADD COLUMN {name} {definition}"
            )
    conn.commit()
    conn.close()
def save_submission(user_id, message_id):
    conn = connect_db()
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(submissions)")
    columns = cur.fetchall()
    fields = []
    values = []
    for column in columns:
        name = column[1]
        not_null = column[3]
        default = column[4]
        primary_key = column[5]
        if primary_key:
            continue
        if name == "user_id":
            fields.append(name)
            values.append(user_id)
        elif name in ("user_message_id", "source_message_id"):
            fields.append(name)
            values.append(message_id)
        elif name == "status":
            fields.append(name)
            values.append("pending")
        elif name == "admin_message_id":
            fields.append(name)
            values.append(None)
        elif not_null and default is None:
            conn.close()
            raise RuntimeError(
                f"Неизвестное обязательное поле базы: {name}"
            )
    cur.execute(
        f"INSERT INTO submissions ({', '.join(fields)}) "
        f"VALUES ({', '.join(['?'] * len(fields))})",
        values,
    )
    submission_id = cur.lastrowid
    conn.commit()
    conn.close()
    return submission_id
def get_submission(submission_id):
    conn = connect_db()
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(submissions)")
    columns = {row[1] for row in cur.fetchall()}
    message_column = (
        "source_message_id"
        if "source_message_id" in columns
        else "user_message_id"
    )
    cur.execute(
        f"""
        SELECT user_id, {message_column}, status
        FROM submissions
        WHERE id = ?
        """,
        (submission_id,),
    )
    result = cur.fetchone()
    conn.close()
    return result
def update_status(submission_id, status):
    conn = connect_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE submissions SET status = ? WHERE id = ?",
        (status, submission_id),
    )
    conn.commit()
    conn.close()
def moderation_keyboard(submission_id):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Опубликовать",
                    callback_data=f"approve:{submission_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=f"reject:{submission_id}",
                ),
            ]
        ]
    )
@dp.message(F.chat.type == "private", F.text == "/start")
async def start_command(message: Message):
    await message.answer(
        "Привет! 👋\n\n"
        "Отправь сюда текст, фото или видео для подслушки. "
        "После проверки сообщение могут опубликовать в канале."
    )
@dp.message(F.chat.type == "private")
async def receive_submission(message: Message):
    if not message.from_user:
        return
    try:
        submission_id = save_submission(
            message.from_user.id,
            message.message_id,
        )
        username = (
            f"@{message.from_user.username}"
            if message.from_user.username
            else "нет"
        )
        info = (
            f"📩 НОВОЕ СООБЩЕНИЕ №{submission_id}\n\n"
            f"👤 Имя: {message.from_user.full_name}\n"
            f"🆔 ID: {message.from_user.id}\n"
            f"🔗 Username: {username}"
        )
        admin_message = await bot.send_message(
            ADMIN_GROUP_ID,
            info,
            reply_markup=moderation_keyboard(submission_id),
        )
        await bot.copy_message(
            chat_id=ADMIN_GROUP_ID,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
            reply_to_message_id=admin_message.message_id,
        )
        await message.answer("✅ Сообщение отправлено на модерацию!")
    except Exception:
        logging.exception("Ошибка при приёме сообщения")
        await message.answer(
            "Не получилось отправить сообщение. Попробуй позже."
        )
async def publish_submission(user_id, message_id):
    original = await bot.forward_message(
        chat_id=ADMIN_GROUP_ID,
        from_chat_id=user_id,
        message_id=message_id,
    )
    try:
        # Копируем содержимое без сведений об авторе.
        # Подпись задаётся прямо в публикации.
        if original.text:
            await bot.send_message(
                chat_id=CHANNEL_ID,
                text=original.text + "\n\n" + FOOTER,
            )
        elif original.caption:
            await bot.copy_message(
                chat_id=CHANNEL_ID,
                from_chat_id=ADMIN_GROUP_ID,
                message_id=original.message_id,
                caption=original.caption + "\n\n" + FOOTER,
            )
        else:
            await bot.copy_message(
                chat_id=CHANNEL_ID,
                from_chat_id=ADMIN_GROUP_ID,
                message_id=original.message_id,
                caption=FOOTER,
            )
    finally:
        try:
            await bot.delete_message(
                chat_id=ADMIN_GROUP_ID,
                message_id=original.message_id,
            )
        except Exception:
            logging.warning("Не удалось удалить временное сообщение")
@dp.callback_query(F.data.startswith("approve:"))
async def approve_submission(callback: CallbackQuery):
    if not callback.message or callback.message.chat.id != ADMIN_GROUP_ID:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    submission_id = int(callback.data.split(":")[1])
    submission = get_submission(submission_id)
    if not submission:
        await callback.answer("Сообщение не найдено.", show_alert=True)
        return
    user_id, message_id, status = submission
    if status != "pending":
        await callback.answer("Сообщение уже обработано.")
        return
    try:
        # Копируем оригинал напрямую из личного чата,
        # не пересылая его в канал от имени пользователя.
        await publish_anonymously(user_id, message_id)
        update_status(submission_id, "approved")
        await callback.message.edit_text(
            callback.message.text + "\n\n✅ ОПУБЛИКОВАНО"
        )
        await callback.answer("Опубликовано!")
    except Exception:
        logging.exception("Ошибка публикации")
        await callback.answer(
            "Не удалось опубликовать. Проверь права бота в канале.",
            show_alert=True,
        )
async def publish_anonymously(user_id, message_id):
    # Telegram позволяет добавлять подпись при копировании медиа.
    # Для текста подпись добавляется в текст публикации.
    source = await bot.forward_message(
        chat_id=ADMIN_GROUP_ID,
        from_chat_id=user_id,
        message_id=message_id,
    )
    try:
        if source.text:
            await bot.send_message(
                CHANNEL_ID,
                source.text + "\n\n" + FOOTER,
            )
        else:
            await bot.copy_message(
                chat_id=CHANNEL_ID,
                from_chat_id=ADMIN_GROUP_ID,
                message_id=source.message_id,
                caption=(source.caption or "") + "\n\n" + FOOTER,
            )
    finally:
        try:
            await bot.delete_message(
                ADMIN_GROUP_ID,
                source.message_id,
            )
        except Exception:
            pass
@dp.callback_query(F.data.startswith("reject:"))
async def reject_submission(callback: CallbackQuery):
    if not callback.message or callback.message.chat.id != ADMIN_GROUP_ID:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    submission_id = int(callback.data.split(":")[1])
    submission = get_submission(submission_id)
    if not submission:
        await callback.answer("Сообщение не найдено.", show_alert=True)
        return
    if submission[2] != "pending":
        await callback.answer("Сообщение уже обработано.")
        return
    update_status(submission_id, "rejected")
    await callback.message.edit_text(
        callback.message.text + "\n\n❌ ОТКЛОНЕНО"
    )
    await callback.answer("Сообщение отклонено.")
async def main():
    init_db()
    await bot.delete_webhook(drop_pending_updates=False)
    await dp.start_polling(bot)
if __name__ == "__main__":
    if not BOT_TOKEN:
        raise RuntimeError("Не задана переменная BOT_TOKEN")
    asyncio.run(main())
