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

FOOTER = "Отправлено с бота @Anon_okotsit_bot"
DB_FILE = "bot.db"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def init_db():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            source_message_id INTEGER,
            status TEXT DEFAULT 'pending',
            admin_message_id INTEGER
        )
    """)

    cur.execute("PRAGMA table_info(submissions)")
    columns = [row[1] for row in cur.fetchall()]

    if "source_message_id" not in columns:
        cur.execute(
            "ALTER TABLE submissions ADD COLUMN source_message_id INTEGER"
        )

    if "admin_message_id" not in columns:
        cur.execute(
            "ALTER TABLE submissions ADD COLUMN admin_message_id INTEGER"
        )

    if "status" not in columns:
        cur.execute(
            "ALTER TABLE submissions ADD COLUMN status TEXT DEFAULT 'pending'"
        )

    conn.commit()
    conn.close()


def save_submission(user_id, source_message_id):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO submissions (user_id, source_message_id, status)
        VALUES (?, ?, 'pending')
        """,
        (user_id, source_message_id),
    )

    submission_id = cur.lastrowid
    conn.commit()
    conn.close()

    return submission_id


def get_submission(submission_id):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute(
        """
        SELECT user_id, source_message_id, status
        FROM submissions
        WHERE id = ?
        """,
        (submission_id,),
    )

    result = cur.fetchone()
    conn.close()

    return result


def update_submission(submission_id, status, admin_message_id=None):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    if admin_message_id is not None:
        cur.execute(
            """
            UPDATE submissions
            SET status = ?, admin_message_id = ?
            WHERE id = ?
            """,
            (status, admin_message_id, submission_id),
        )
    else:
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


@dp.message(F.chat.type == "private")
async def receive_submission(message: Message):
    if message.from_user is None:
        return

    submission_id = save_submission(
        message.from_user.id,
        message.message_id,
    )

    username = (
        f"@{message.from_user.username}"
        if message.from_user.username
        else "нет"
    )

    full_name = message.from_user.full_name

    info = (
        f"📩 НОВОЕ СООБЩЕНИЕ №{submission_id}\n\n"
        f"👤 Имя: {full_name}\n"
        f"🆔 ID: {message.from_user.id}\n"
        f"🔗 Username: {username}"
    )

    try:
        admin_message = await bot.send_message(
            ADMIN_GROUP_ID,
            info,
            reply_markup=moderation_keyboard(submission_id),
        )

        update_submission(
            submission_id,
            "pending",
            admin_message.message_id,
        )

        await bot.copy_message(
            chat_id=ADMIN_GROUP_ID,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
            reply_to_message_id=admin_message.message_id,
        )

        await message.answer("✅ Сообщение отправлено на модерацию!")

    except Exception:
        logging.exception("Ошибка при передаче сообщения модераторам")
        await message.answer(
            "Не получилось отправить сообщение. Попробуй ещё раз позже."
        )


@dp.callback_query(F.data.startswith("approve:"))
async def approve_submission(callback: CallbackQuery):
    if callback.message is None:
        await callback.answer()
        return

    if callback.message.chat.id != ADMIN_GROUP_ID:
        await callback.answer("Нет доступа.", show_alert=True)
        return

    submission_id = int(callback.data.split(":")[1])
    submission = get_submission(submission_id)

    if not submission:
        await callback.answer("Сообщение не найдено.", show_alert=True)
        return

    user_id, source_message_id, status = submission

    if status != "pending":
        await callback.answer("Это сообщение уже обработано.")
        return

    try:
        await bot.copy_message(
            chat_id=CHANNEL_ID,
            from_chat_id=user_id,
            message_id=source_message_id,
        )

        await bot.send_message(CHANNEL_ID, FOOTER)

        update_submission(submission_id, "approved")

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


@dp.callback_query(F.data.startswith("reject:"))
async def reject_submission(callback: CallbackQuery):
    if callback.message is None:
        await callback.answer()
        return

    if callback.message.chat.id != ADMIN_GROUP_ID:
        await callback.answer("Нет доступа.", show_alert=True)
        return

    submission_id = int(callback.data.split(":")[1])
    submission = get_submission(submission_id)

    if not submission:
        await callback.answer("Сообщение не найдено.", show_alert=True)
        return

    user_id, source_message_id, status = submission

    if status != "pending":
        await callback.answer("Это сообщение уже обработано.")
        return

    update_submission(submission_id, "rejected")

    await callback.message.edit_text(
        callback.message.text + "\n\n❌ ОТКЛОНЕНО"
    )

    await callback.answer("Сообщение отклонено.")


@dp.message(F.chat.type == "private", F.text == "/start")
async def start_command(message: Message):
    await message.answer(
        "Привет! 👋\n\n"
        "Отправь сюда сообщение, фото или видео для подслушки. "
        "После модерации его могут опубликовать в канале."
    )


async def main():
    init_db()
    await bot.delete_webhook(drop_pending_updates=False)
    await dp.start_polling(bot)


if __name__ == "__main__":
    if not BOT_TOKEN:
        raise RuntimeError("Не задана переменная BOT_TOKEN")

    asyncio.run(main())
