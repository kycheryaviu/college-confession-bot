import asyncio
import html
import logging
import os
import sqlite3
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
# ==================================================
# НАСТРОЙКИ
# ==================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_GROUP_ID = int(os.getenv("ADMIN_GROUP_ID", "-1004412864686"))
CHANNEL_ID = os.getenv("CHANNEL_ID", "@okotsitomsk")
BOT_USERNAME = "Anon_okotsit_bot"
BOT_LINK = f"https://t.me/{BOT_USERNAME}"
CHANNEL_LINK = "https://t.me/okotsitomsk"
DB_PATH = os.getenv("DB_PATH", "bot.db")
HEADER = f'<a href="{CHANNEL_LINK}">ПОДСЛУШКА ОКОТСиТ</a>'
FOOTER = f'<a href="{BOT_LINK}">Написать в подслушку</a>'
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
if not BOT_TOKEN:
    raise RuntimeError("Не задана переменная окружения BOT_TOKEN")
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()
# ==================================================
# БАЗА ДАННЫХ
# ==================================================
def db_connect():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    return con
def init_db():
    with db_connect() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS bot_submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                full_name TEXT,
                user_message_id INTEGER NOT NULL,
                message_type TEXT NOT NULL,
                text_content TEXT,
                caption TEXT,
                moderation_message_id INTEGER,
                content_group_message_id INTEGER,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS group_message_map (
                group_message_id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                submission_id INTEGER
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS admin_reply_map (
                bot_message_id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                submission_id INTEGER
            )
        """)
        con.execute("""
            CREATE INDEX IF NOT EXISTS idx_submissions_user
            ON bot_submissions(user_id)
        """)
        con.commit()
def save_submission(
    user_id,
    username,
    full_name,
    user_message_id,
    message_type,
    text_content,
    caption,
):
    with db_connect() as con:
        cur = con.execute("""
            INSERT INTO bot_submissions (
                user_id,
                username,
                full_name,
                user_message_id,
                message_type,
                text_content,
                caption,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
        """, (
            user_id,
            username,
            full_name,
            user_message_id,
            message_type,
            text_content,
            caption,
        ))
        con.commit()
        return cur.lastrowid
def get_submission(submission_id):
    with db_connect() as con:
        return con.execute("""
            SELECT *
            FROM bot_submissions
            WHERE id = ?
        """, (submission_id,)).fetchone()
def change_status(submission_id, new_status):
    with db_connect() as con:
        cur = con.execute("""
            UPDATE bot_submissions
            SET status = ?
            WHERE id = ? AND status = 'pending'
        """, (new_status, submission_id))
        con.commit()
        return cur.rowcount == 1
def reset_status(submission_id):
    with db_connect() as con:
        con.execute("""
            UPDATE bot_submissions
            SET status = 'pending'
            WHERE id = ? AND status = 'processing'
        """, (submission_id,))
        con.commit()
def save_group_mapping(message_id, user_id, submission_id):
    with db_connect() as con:
        con.execute("""
            INSERT OR REPLACE INTO group_message_map (
                group_message_id,
                user_id,
                submission_id
            )
            VALUES (?, ?, ?)
        """, (message_id, user_id, submission_id))
        con.commit()
def get_group_mapping(message_id):
    with db_connect() as con:
        return con.execute("""
            SELECT *
            FROM group_message_map
            WHERE group_message_id = ?
        """, (message_id,)).fetchone()
def save_admin_reply(bot_message_id, user_id, submission_id):
    with db_connect() as con:
        con.execute("""
            INSERT OR REPLACE INTO admin_reply_map (
                bot_message_id,
                user_id,
                submission_id
            )
            VALUES (?, ?, ?)
        """, (bot_message_id, user_id, submission_id))
        con.commit()
def get_admin_reply(bot_message_id):
    with db_connect() as con:
        return con.execute("""
            SELECT *
            FROM admin_reply_map
            WHERE bot_message_id = ?
        """, (bot_message_id,)).fetchone()
# ==================================================
# КНОПКИ МОДЕРАЦИИ
# ==================================================
def moderation_keyboard(submission_id):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Одобрить",
                    callback_data=f"approve:{submission_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=f"reject:{submission_id}",
                ),
            ]
        ]
    )
def status_text(submission, status):
    username = (
        f"@{html.escape(submission['username'])}"
        if submission["username"]
        else "не указан"
    )
    full_name = html.escape(submission["full_name"] or "Без имени")
    if status == "approved":
        result = "✅ <b>ОДОБРЕНО</b>"
    elif status == "rejected":
        result = "❌ <b>НЕ ОДОБРЕНО</b>"
    else:
        result = "⏳ <b>НА МОДЕРАЦИИ</b>"
    return (
        f"📩 <b>Сообщение №{submission['id']}</b>\n\n"
        f"👤 Отправитель: {full_name}\n"
        f"🔗 Username: {username}\n"
        f"🆔 Telegram ID: <code>{submission['user_id']}</code>\n"
        f"📌 Статус: {result}\n\n"
        "Чтобы ответить студенту, ответьте на его сообщение "
        "в этой группе через функцию «Ответить»."
    )
# ==================================================
# ОФОРМЛЕНИЕ ПУБЛИКАЦИИ
# ==================================================
def publication_text(text):
    safe_text = html.escape(text or "")
    return (
        f"{HEADER}\n\n"
        f"<blockquote>{safe_text}</blockquote>\n\n"
        f"{FOOTER}"
    )
def publication_caption(caption):
    safe_caption = html.escape(caption or "")
    if len(safe_caption) > 700:
        safe_caption = safe_caption[:697] + "..."
    if safe_caption:
        return (
            f"{HEADER}\n\n"
            f"<blockquote>{safe_caption}</blockquote>\n\n"
            f"{FOOTER}"
        )
    return f"{HEADER}\n\n{FOOTER}"
async def publish_submission(submission):
    user_id = submission["user_id"]
    source_message_id = submission["user_message_id"]
    message_type = submission["message_type"]
    if message_type == "text":
        text = publication_text(submission["text_content"])
        if len(text) > 4096:
            raise ValueError(
                "Текст слишком длинный для публикации "
                "с оформлением в одном сообщении."
            )
        await bot.send_message(
            chat_id=CHANNEL_ID,
            text=text,
            link_preview_options={"is_disabled": True},
        )
        return
    no_caption_types = {
        "sticker",
        "video_note",
        "voice",
        "contact",
        "location",
        "venue",
        "poll",
        "dice",
        "game",
    }
    if message_type in no_caption_types:
        copied = await bot.copy_message(
            chat_id=CHANNEL_ID,
            from_chat_id=user_id,
            message_id=source_message_id,
        )
        await bot.send_message(
            chat_id=CHANNEL_ID,
            text=f"{HEADER}\n\n{FOOTER}",
            reply_to_message_id=copied.message_id,
            link_preview_options={"is_disabled": True},
        )
        return
    caption = publication_caption(submission["caption"])
    await bot.copy_message(
        chat_id=CHANNEL_ID,
        from_chat_id=user_id,
        message_id=source_message_id,
        caption=caption,
        parse_mode=ParseMode.HTML,
        show_caption_above_media=True,
    )
# ==================================================
# СООБЩЕНИЯ ПОЛЬЗОВАТЕЛЕЙ
# ==================================================
@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "👋 Добро пожаловать в подслушку ОКОТСиТ!\n\n"
        "Отправьте сообщение, фото или видео — "
        "оно будет передано модераторам.\n\n"
        "После проверки мы сообщим вам результат."
    )
def get_message_type(message: Message):
    if message.text:
        return "text"
    if message.photo:
        return "photo"
    if message.video:
        return "video"
    if message.animation:
        return "animation"
    if message.document:
        return "document"
    if message.audio:
        return "audio"
    if message.voice:
        return "voice"
    if message.video_note:
        return "video_note"
    if message.sticker:
        return "sticker"
    if message.contact:
        return "contact"
    if message.location:
        return "location"
    if message.venue:
        return "venue"
    if message.poll:
        return "poll"
    if message.dice:
        return "dice"
    if message.game:
        return "game"
    return "other"
async def deliver_student_reply(message: Message, mapping):
    user_id = mapping["user_id"]
    submission_id = mapping["submission_id"]
    name = html.escape(message.from_user.full_name or "Без имени")
    username = (
        f"@{html.escape(message.from_user.username)}"
        if message.from_user.username
        else "не указан"
    )
    meta = await bot.send_message(
        chat_id=ADMIN_GROUP_ID,
        text=(
            "↩️ <b>ОТВЕТ СТУДЕНТА</b>\n\n"
            f"👤 {name}\n"
            f"🔗 Username: {username}\n"
            f"🆔 Telegram ID: <code>{user_id}</code>\n"
            f"📩 По заявке: №{submission_id or '—'}\n\n"
            "Ответьте на сообщение студента, чтобы написать ему."
        ),
    )
    save_group_mapping(meta.message_id, user_id, submission_id)
    copied = await bot.copy_message(
        chat_id=ADMIN_GROUP_ID,
        from_chat_id=message.chat.id,
        message_id=message.message_id,
        reply_to_message_id=meta.message_id,
    )
    save_group_mapping(copied.message_id, user_id, submission_id)
    await message.answer(
        "✅ Ваш ответ передан администрации."
    )
@dp.message(F.chat.type == "private")
async def private_message(message: Message):
    if not message.from_user:
        return
    user_id = message.from_user.id
    if message.reply_to_message:
        mapping = get_admin_reply(
            message.reply_to_message.message_id
        )
        if mapping and mapping["user_id"] == user_id:
            await deliver_student_reply(message, mapping)
            return
    if message.text and message.text.startswith("/"):
        await message.answer(
            "Чтобы отправить сообщение в подслушку, "
            "напишите его обычным текстом."
        )
        return
    message_type = get_message_type(message)
    text_content = message.text or message.caption or ""
    caption = message.caption or ""
    submission_id = save_submission(
        user_id=user_id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
        user_message_id=message.message_id,
        message_type=message_type,
        text_content=text_content,
        caption=caption,
    )
    metadata = await bot.send_message(
        chat_id=ADMIN_GROUP_ID,
        text=(
            f"📩 <b>НОВАЯ ЗАЯВКА №{submission_id}</b>\n\n"
            f"👤 Отправитель: "
            f"{html.escape(message.from_user.full_name or 'Без имени')}\n"
            f"🔗 Username: "
            f"{'@' + html.escape(message.from_user.username) if message.from_user.username else 'не указан'}\n"
            f"🆔 Telegram ID: <code>{user_id}</code>\n"
            f"📌 Статус: ⏳ <b>НА МОДЕРАЦИИ</b>\n\n"
            "Ответьте на содержимое заявки, чтобы написать студенту."
        ),
        reply_markup=moderation_keyboard(submission_id),
    )
    with db_connect() as con:
        con.execute("""
            UPDATE bot_submissions
            SET moderation_message_id = ?
            WHERE id = ?
        """, (metadata.message_id, submission_id))
        con.commit()
    save_group_mapping(metadata.message_id, user_id, submission_id)
    try:
        copied = await bot.copy_message(
            chat_id=ADMIN_GROUP_ID,
            from_chat_id=user_id,
            message_id=message.message_id,
            reply_to_message_id=metadata.message_id,
        )
        with db_connect() as con:
            con.execute("""
                UPDATE bot_submissions
                SET content_group_message_id = ?
                WHERE id = ?
            """, (copied.message_id, submission_id))
            con.commit()
        save_group_mapping(copied.message_id, user_id, submission_id)
    except TelegramAPIError:
        logger.exception("Не удалось скопировать сообщение в модерацию")
        await bot.send_message(
            chat_id=ADMIN_GROUP_ID,
            text=(
                f"⚠️ Не удалось скопировать содержимое заявки "
                f"№{submission_id}. Проверьте доступ бота."
            ),
            reply_to_message_id=metadata.message_id,
        )
        await message.answer(
            "⚠️ Не удалось передать сообщение на модерацию. "
            "Попробуйте отправить его ещё раз."
        )
        return
    await message.answer(
        "📩 Ваше сообщение принято и отправлено на модерацию."
    )
# ==================================================
# ОДОБРЕНИЕ И ОТКЛОНЕНИЕ
# ==================================================
@dp.callback_query(F.data.startswith("approve:"))
async def approve_submission(callback: CallbackQuery):
    submission_id = int(callback.data.split(":")[1])
    submission = get_submission(submission_id)
    if not submission:
        await callback.answer("Заявка не найдена.", show_alert=True)
        return
    if submission["status"] != "pending":
        await callback.answer(
            f"Заявка уже обработана: {submission['status']}",
            show_alert=True,
        )
        return
    if not change_status(submission_id, "processing"):
        await callback.answer(
            "Заявка уже обрабатывается.",
            show_alert=True,
        )
        return
    await callback.answer("Публикуем сообщение...")
    try:
        await publish_submission(submission)
        with db_connect() as con:
            con.execute("""
                UPDATE bot_submissions
                SET status = 'approved'
                WHERE id = ?
            """, (submission_id,))
            con.commit()
        await bot.send_message(
            chat_id=submission["user_id"],
            text="✅ Ваше сообщение одобрено модераторами!",
        )
        await callback.message.edit_text(
            status_text(submission, "approved"),
            reply_markup=None,
        )
    except Exception:
        logger.exception("Ошибка публикации заявки №%s", submission_id)
        reset_status(submission_id)
        await callback.message.answer(
            f"⚠️ Не удалось опубликовать заявку №{submission_id}. "
            "Статус сброшен, можно попробовать снова."
        )
        await callback.answer(
            "Ошибка публикации. Проверьте права бота и журнал.",
            show_alert=True,
        )
@dp.callback_query(F.data.startswith("reject:"))
async def reject_submission(callback: CallbackQuery):
    submission_id = int(callback.data.split(":")[1])
    submission = get_submission(submission_id)
    if not submission:
        await callback.answer("Заявка не найдена.", show_alert=True)
        return
    if submission["status"] != "pending":
        await callback.answer(
            f"Заявка уже обработана: {submission['status']}",
            show_alert=True,
        )
        return
    if not change_status(submission_id, "rejected"):
        await callback.answer(
            "Заявка уже обрабатывается.",
            show_alert=True,
        )
        return
    try:
        await bot.send_message(
            chat_id=submission["user_id"],
            text=(
                "❌ К сожалению, ваше сообщение "
                "не одобрено модераторами."
            ),
        )
    except TelegramAPIError:
        logger.exception(
            "Не удалось уведомить пользователя о заявке №%s",
            submission_id,
        )
    await callback.message.edit_text(
        status_text(submission, "rejected"),
        reply_markup=None,
    )
    await callback.answer("Заявка отклонена.")
# ==================================================
# ОТВЕТ АДМИНИСТРАТОРА СТУДЕНТУ
# ==================================================
@dp.message(F.chat.id == ADMIN_GROUP_ID, F.reply_to_message)
async def admin_reply(message: Message):
    if not message.from_user or message.from_user.is_bot:
        return
    replied_message_id = message.reply_to_message.message_id
    mapping = get_group_mapping(replied_message_id)
    if not mapping:
        return
    user_id = mapping["user_id"]
    submission_id = mapping["submission_id"]
    try:
        if message.text:
            sent = await bot.send_message(
                chat_id=user_id,
                text=(
                    "💬 <b>Вам ответила администрация подслушки:</b>\n\n"
                    f"{html.escape(message.text)}"
                ),
            )
        elif message.caption:
            sent = await bot.send_message(
                chat_id=user_id,
                text=(
                    "💬 <b>Вам ответила администрация подслушки:</b>\n\n"
                    f"{html.escape(message.caption)}"
                ),
            )
        else:
            await bot.send_message(
                chat_id=user_id,
                text="💬 <b>Вам ответила администрация подслушки:</b>",
            )
            sent_copy = await bot.copy_message(
                chat_id=user_id,
                from_chat_id=ADMIN_GROUP_ID,
                message_id=message.message_id,
            )
            save_admin_reply(
                sent_copy.message_id,
                user_id,
                submission_id,
            )
            await message.reply(
                "✅ Ответ отправлен пользователю."
            )
            return
        save_admin_reply(
            sent.message_id,
            user_id,
            submission_id,
        )
        await message.reply("✅ Ответ отправлен пользователю.")
    except TelegramAPIError:
        logger.exception("Не удалось отправить ответ студенту")
        await message.reply(
            "⚠️ Не удалось доставить ответ. "
            "Возможно, пользователь не запускал бота "
            "или заблокировал его."
        )
# ==================================================
# ЗАПУСК
# ==================================================
async def main():
    init_db()
    logger.info("Бот запускается...")
    await bot.delete_webhook(drop_pending_updates=False)
    await dp.start_polling(bot)
if __name__ == "__main__":
    asyncio.run(main())
