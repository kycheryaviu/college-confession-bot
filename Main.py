import os
import sqlite3
import asyncio

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.exceptions import TelegramBadRequest


# =========================
# НАСТРОЙКИ
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_GROUP_ID = int(os.getenv("ADMIN_GROUP_ID", "-1004412864686"))
CHANNEL_ID = os.getenv("CHANNEL_ID", "@okotsitomsk")

BOT_USERNAME = "Anon_okotsit_bot"
FOOTER = f"📩 Отправлено с бота @{BOT_USERNAME}"

if not BOT_TOKEN:
    raise RuntimeError("Не задана переменная окружения BOT_TOKEN")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

DB_PATH = "bot.db"


# =========================
# БАЗА ДАННЫХ
# =========================

def init_db():
    with sqlite3.connect(DB_PATH) as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                source_message_id INTEGER NOT NULL,
                status TEXT DEFAULT 'pending',
                admin_copy_id INTEGER
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS message_links (
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                submission_id INTEGER NOT NULL,
                PRIMARY KEY (chat_id, message_id)
            )
        """)


def create_submission(user_id, source_message_id):
    with sqlite3.connect(DB_PATH) as db:
        cursor = db.execute(
            """
            INSERT INTO submissions
            (user_id, source_message_id, status)
            VALUES (?, ?, 'pending')
            """,
            (user_id, source_message_id),
        )
        return cursor.lastrowid


def get_submission(submission_id):
    with sqlite3.connect(DB_PATH) as db:
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT * FROM submissions WHERE id = ?",
            (submission_id,),
        ).fetchone()
        return dict(row) if row else None


def set_status(submission_id, status):
    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            "UPDATE submissions SET status = ? WHERE id = ?",
            (status, submission_id),
        )


def set_admin_copy_id(submission_id, message_id):
    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            "UPDATE submissions SET admin_copy_id = ? WHERE id = ?",
            (message_id, submission_id),
        )


def link_message(chat_id, message_id, submission_id):
    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            """
            INSERT OR REPLACE INTO message_links
            (chat_id, message_id, submission_id)
            VALUES (?, ?, ?)
            """,
            (chat_id, message_id, submission_id),
        )


def find_linked_submission(chat_id, message_id):
    with sqlite3.connect(DB_PATH) as db:
        row = db.execute(
            """
            SELECT submission_id FROM message_links
            WHERE chat_id = ? AND message_id = ?
            """,
            (chat_id, message_id),
        ).fetchone()

        return row[0] if row else None


# =========================
# КЛАВИАТУРА МОДЕРАЦИИ
# =========================

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


# =========================
# ПРОВЕРКА АДМИНИСТРАТОРА
# =========================

async def is_admin(user_id):
    try:
        member = await bot.get_chat_member(
            ADMIN_GROUP_ID,
            user_id,
        )
        return member.status in ("administrator", "creator")
    except Exception:
        return False


# =========================
# /START
# =========================

@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "👋 Привет!\n\n"
        "Это анонимная подслушка колледжа.\n\n"
        "Отправь мне сообщение, фото, видео или голосовое — "
        "оно попадёт на модерацию.\n\n"
        "После одобрения его опубликуют в канале.\n"
        "Твоё имя и Telegram ID в канале не показываются."
    )


# =========================
# КОПИРОВАНИЕ СООБЩЕНИЯ
# =========================

async def copy_with_footer(
    destination_chat_id,
    source_chat_id,
    source_message_id,
    original_message=None,
):
    """
    Текстовые сообщения публикуются с подписью.
    Для медиа подпись добавляется к самому медиа,
    если Telegram поддерживает caption для этого типа.
    Для остальных типов отправляется отдельное сообщение
    с подписью.
    """

    if original_message and original_message.text:
        text = original_message.text
        result = await bot.send_message(
            destination_chat_id,
            f"{text}\n\n{FOOTER}",
        )
        return result.message_id

    caption_types = {
        "photo",
        "video",
        "animation",
        "document",
        "audio",
        "voice",
    }

    content_type = (
        original_message.content_type
        if original_message
        else None
    )

    if content_type in caption_types:
        original_caption = (
            original_message.caption or ""
        )

        # У Telegram есть ограничение на длину подписи к медиа.
        max_caption_length = 1024
        available = max_caption_length - len(FOOTER) - 2

        caption = original_caption[:available]

        if caption:
            caption = f"{caption}\n\n{FOOTER}"
        else:
            caption = FOOTER

        copied = await bot.copy_message(
            chat_id=destination_chat_id,
            from_chat_id=source_chat_id,
            message_id=source_message_id,
            caption=caption,
        )
        return copied.message_id

    copied = await bot.copy_message(
        chat_id=destination_chat_id,
        from_chat_id=source_chat_id,
        message_id=source_message_id,
    )

    # Стикеры, видеосообщения и некоторые другие типы
    # не поддерживают подпись непосредственно к сообщению.
    await bot.send_message(
        destination_chat_id,
        FOOTER,
    )

    return copied.message_id


# =========================
# ПРИЁМ СООБЩЕНИЙ СТУДЕНТОВ
# =========================

@dp.message(F.chat.type == "private")
async def receive_private_message(message: Message):
    if message.from_user is None:
        return

    user_id = message.from_user.id

    # Если студент отвечает на сообщение администратора,
    # ранее доставленное через бота, отправляем ответ в группу.
    if message.reply_to_message:
        linked_id = find_linked_submission(
            user_id,
            message.reply_to_message.message_id,
        )

        if linked_id:
            await relay_student_reply(message, linked_id)
            return

    submission_id = create_submission(
        user_id,
        message.message_id,
    )

    username = (
        f"@{message.from_user.username}"
        if message.from_user.username
        else "не указан"
    )

    header = await bot.send_message(
        ADMIN_GROUP_ID,
        (
            "📨 НОВОЕ АНОНИМНОЕ СООБЩЕНИЕ\n\n"
            f"🆔 Заявка: #{submission_id}\n"
            f"👤 Имя: {message.from_user.full_name}\n"
            f"🔗 Username: {username}\n"
            f"🪪 Telegram ID: {user_id}\n\n"
            "Чтобы ответить студенту, ответьте на его сообщение "
            "ниже в этой группе."
        ),
        reply_markup=moderation_keyboard(submission_id),
    )

    link_message(
        ADMIN_GROUP_ID,
        header.message_id,
        submission_id,
    )

    copied = await bot.copy_message(
        chat_id=ADMIN_GROUP_ID,
        from_chat_id=user_id,
        message_id=message.message_id,
    )

    set_admin_copy_id(
        submission_id,
        copied.message_id,
    )

    link_message(
        ADMIN_GROUP_ID,
        copied.message_id,
        submission_id,
    )

    await message.answer(
        "✅ Сообщение отправлено на модерацию!\n"
        "После одобрения оно появится в канале."
    )


# =========================
# ОТВЕТ СТУДЕНТУ
# =========================

async def relay_admin_reply(message: Message, submission_id):
    submission = get_submission(submission_id)

    if not submission:
        return

    user_id = submission["user_id"]

    try:
        delivered = await bot.copy_message(
            chat_id=user_id,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
        )

        link_message(
            user_id,
            delivered.message_id,
            submission_id,
        )

    except Exception:
        await message.reply(
            "Не удалось доставить ответ. Возможно, "
            "студент не запускал бота или заблокировал его."
        )


# =========================
# ОТВЕТ СТУДЕНТА АДМИНИСТРАТОРУ
# =========================

async def relay_student_reply(message: Message, submission_id):
    user = message.from_user

    username = (
        f"@{user.username}"
        if user and user.username
        else "не указан"
    )

    header = await bot.send_message(
        ADMIN_GROUP_ID,
        (
            f"↩️ ОТВЕТ СТУДЕНТА НА ЗАЯВКУ #{submission_id}\n\n"
            f"👤 Имя: {user.full_name if user else 'неизвестно'}\n"
            f"🔗 Username: {username}\n"
            f"🪪 Telegram ID: {user.id if user else 'неизвестно'}\n\n"
            "Ответьте на сообщение студента ниже, "
            "чтобы продолжить диалог."
        ),
    )

    link_message(
        ADMIN_GROUP_ID,
        header.message_id,
        submission_id,
    )

    copied = await bot.copy_message(
        chat_id=ADMIN_GROUP_ID,
        from_chat_id=message.chat.id,
        message_id=message.message_id,
    )

    link_message(
        ADMIN_GROUP_ID,
        copied.message_id,
        submission_id,
    )

    await message.answer("📨 Ответ отправлен администраторам.")


# =========================
# ОБРАБОТКА СООБЩЕНИЙ В ГРУППЕ
# =========================

@dp.message(F.chat.id == ADMIN_GROUP_ID)
async def admin_group_messages(message: Message):
    if not message.from_user:
        return

    # Не обрабатываем сообщения самого бота.
    if message.from_user.id == (await bot.me()).id:
        return

    if not message.reply_to_message:
        return

    # Отвечать студентам могут только администраторы группы.
    if not await is_admin(message.from_user.id):
        return

    submission_id = find_linked_submission(
        ADMIN_GROUP_ID,
        message.reply_to_message.message_id,
    )

    if submission_id:
        await relay_admin_reply(
            message,
            submission_id,
        )


# =========================
# ОДОБРЕНИЕ / ОТКЛОНЕНИЕ
# =========================

@dp.callback_query(F.data.startswith("approve:"))
async def approve_submission(callback: CallbackQuery):
    if not callback.from_user:
        return

    if not await is_admin(callback.from_user.id):
        await callback.answer(
            "Только администратор может одобрить сообщение.",
            show_alert=True,
        )
        return

    submission_id = int(callback.data.split(":")[1])
    submission = get_submission(submission_id)

    if not submission:
        await callback.answer("Заявка не найдена.", show_alert=True)
        return

    if submission["status"] != "pending":
        await callback.answer("Заявка уже обработана.")
        return

    user_id = submission["user_id"]
    source_message_id = submission["source_message_id"]

    try:
        original = await bot.forward_message(
            chat_id=callback.message.chat.id,
            from_chat_id=user_id,
            message_id=source_message_id,
        )

        # Удаляем техническую пересылку из группы:
        # её содержимое не должно попадать в канал.
        await bot.delete_message(
            callback.message.chat.id,
            original.message_id,
        )

        source = await bot.forward_message(
            chat_id=user_id,
            from_chat_id=user_id,
            message_id=source_message_id,
        )

        # Сохраняем тип сообщения и подпись,
        # не публикуя пересланную копию.
        try:
            await bot.delete_message(
                user_id,
                source.message_id,
            )
        except Exception:
            pass

        # Получаем исходное сообщение из личного чата.
        # Для публикации используем его тип и оригинальную подпись.
        await publish_submission(
            user_id,
            source_message_id,
        )

        set_status(submission_id, "approved")

        await callback.message.edit_reply_markup(
            reply_markup=None
        )

        await callback.answer("Опубликовано!")

    except Exception as error:
        await callback.answer(
            "Не удалось опубликовать сообщение. "
            "Проверьте права бота в канале.",
            show_alert=True,
        )
        print("Ошибка публикации:", repr(error))


async def publish_submission(user_id, source_message_id):
    # Получаем исходное сообщение через копирование
    # в технический чат здесь не требуется.
    # В канал публикуем оригинал без пересылки.
    source = await bot.get_chat(user_id)

    # Для текста и медиа используем copy_message.
    # Сначала получаем тип сообщения из сохранённого исходника.
    # Исходное сообщение доступно в чате пользователя,
    # поэтому можно скопировать его напрямую.
    #
    # Чтобы сохранить исходный текст и подпись, для текстовых
    # сообщений используем копирование с отдельным футером.
    #
    # Тип исходного сообщения нужно передать из обработчика,
    # поэтому здесь используем копирование с подписью.
    await bot.copy_message(
        chat_id=CHANNEL_ID,
        from_chat_id=user_id,
        message_id=source_message_id,
    )

    await bot.send_message(
        CHANNEL_ID,
        FOOTER,
    )


@dp.callback_query(F.data.startswith("reject:"))
async def reject_submission(callback: CallbackQuery):
    if not callback.from_user:
        return

    if not await is_admin(callback.from_user.id):
        await callback.answer(
            "Только администратор может отклонить сообщение.",
            show_alert=True,
        )
        return

    submission_id = int(callback.data.split(":")[1])
    submission = get_submission(submission_id)

    if not submission:
        await callback.answer("Заявка не найдена.", show_alert=True)
        return

    if submission["status"] != "pending":
        await callback.answer("Заявка уже обработана.")
        return

    set_status(submission_id, "rejected")

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    try:
        await bot.send_message(
            submission["user_id"],
            "❌ К сожалению, твоё сообщение не прошло модерацию.",
        )
    except Exception:
        pass

    await callback.answer("Заявка отклонена.")


# =========================
# ЗАПУСК
# =========================

async def main():
    init_db()
    print("Бот запускается...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
