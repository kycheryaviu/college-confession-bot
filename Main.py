import os
import asyncio
import sqlite3
from html import escape

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

TOKEN = os.getenv("BOT_TOKEN")
GROUP_ID = int(os.getenv("ADMIN_GROUP_ID", "0"))
CHANNEL_ID = os.getenv("CHANNEL_ID", "")

if not TOKEN or not GROUP_ID or not CHANNEL_ID:
    raise RuntimeError("Проверь переменные окружения бота!")

bot = Bot(token=TOKEN)
dp = Dispatcher()

db = sqlite3.connect("bot.db")
db.execute("""
CREATE TABLE IF NOT EXISTS submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    user_message_id INTEGER NOT NULL,
    admin_copy_id INTEGER,
    status TEXT NOT NULL DEFAULT 'pending'
)
""")
db.commit()


def buttons(sub_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Одобрить",
                callback_data=f"approve:{sub_id}"
            ),
            InlineKeyboardButton(
                text="❌ Отклонить",
                callback_data=f"reject:{sub_id}"
            )
        ],
        [
            InlineKeyboardButton(
                text="💬 Ответить автору",
                callback_data=f"reply:{sub_id}"
            )
        ]
    ])


async def is_admin(user_id):
    try:
        member = await bot.get_chat_member(GROUP_ID, user_id)
        return member.status in ("administrator", "creator")
    except Exception:
        return False


async def send_submission(message, is_dialog=False):
    user = message.from_user

    if not user:
        return

    cur = db.execute(
        "INSERT INTO submissions "
        "(user_id, user_message_id, status) VALUES (?, ?, ?)",
        (
            user.id,
            message.message_id,
            "open" if is_dialog else "pending"
        )
    )
    sub_id = cur.lastrowid
    db.commit()

    username = f"@{user.username}" if user.username else "нет"

    if is_dialog:
        header = (
            f"💬 ОТВЕТ СТУДЕНТА\n"
            f"Имя: {user.full_name}\n"
            f"ID: {user.id}\n"
            f"Username: {username}\n"
            f"Заявка №{sub_id}\n\n"
            f"Чтобы ответить, нажми «Ответить» "
            f"на сообщение студента ниже."
        )
        await bot.send_message(GROUP_ID, header)
    else:
        header = (
            f"📩 НОВАЯ ПРЕДЛОЖКА\n"
            f"Имя: {user.full_name}\n"
            f"ID: {user.id}\n"
            f"Username: {username}\n"
            f"Заявка №{sub_id}"
        )
        await bot.send_message(
            GROUP_ID,
            header,
            reply_markup=buttons(sub_id)
        )

    copied = await bot.copy_message(
        chat_id=GROUP_ID,
        from_chat_id=message.chat.id,
        message_id=message.message_id
    )

    db.execute(
        "UPDATE submissions SET admin_copy_id = ? WHERE id = ?",
        (copied.message_id, sub_id)
    )
    db.commit()

    if not is_dialog:
        await message.answer(
            "✅ Предложка отправлена на модерацию!"
        )


@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "Привет! 👋 Это подслушка колледжа.\n\n"
        "Отправь текст, фото, видео или голосовое сообщение. "
        "Предложки проверяют администраторы.\n\n"
        "Если администратор начнёт с тобой диалог, "
        "ты сможешь отвечать прямо здесь."
    )


@dp.message(F.chat.type == "private")
async def private_message(message: Message):
    user = message.from_user

    if not user:
        return

    row = db.execute(
        "SELECT id FROM submissions "
        "WHERE user_id = ? AND status = 'open' "
        "ORDER BY id DESC LIMIT 1",
        (user.id,)
    ).fetchone()

    try:
        await send_submission(
            message,
            is_dialog=bool(row)
        )
    except Exception:
        await message.answer(
            "Не получилось отправить сообщение. "
            "Попробуй ещё раз позже."
        )


@dp.callback_query(F.data.startswith(("approve:", "reject:", "reply:")))
async def moderate(callback: CallbackQuery):
    if not callback.message or not callback.from_user:
        await callback.answer()
        return

    if callback.message.chat.id != GROUP_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return

    if not await is_admin(callback.from_user.id):
        await callback.answer(
            "Только администраторы могут выполнять это действие.",
            show_alert=True
        )
        return

    action, sub_id_text = callback.data.split(":", 1)

    try:
        sub_id = int(sub_id_text)
    except ValueError:
        await callback.answer("Неверный номер заявки.")
        return

    row = db.execute(
        "SELECT user_id, user_message_id, status "
        "FROM submissions WHERE id = ?",
        (sub_id,)
    ).fetchone()

    if not row:
        await callback.answer("Заявка не найдена.", show_alert=True)
        return

    user_id, user_message_id, status = row

    if action == "approve":
        if status != "pending":
            await callback.answer("Заявка уже обработана.")
            return

        try:
            await bot.copy_message(
                chat_id=CHANNEL_ID,
                from_chat_id=user_id,
                message_id=user_message_id
            )
            db.execute(
                "UPDATE submissions SET status = 'published' WHERE id = ?",
                (sub_id,)
            )
            db.commit()

            await bot.send_message(
                user_id,
                "✅ Твою предложку одобрили и опубликовали!"
            )
            await callback.message.edit_reply_markup(
                reply_markup=None
            )
            await callback.answer("Опубликовано!")
        except Exception:
            await callback.answer(
                "Не удалось опубликовать. Проверь права бота в канале.",
                show_alert=True
            )

    elif action == "reject":
        if status != "pending":
            await callback.answer("Заявка уже обработана.")
            return

        db.execute(
            "UPDATE submissions SET status = 'rejected' WHERE id = ?",
            (sub_id,)
        )
        db.commit()

        try:
            await bot.send_message(
                user_id,
                "❌ Твою предложку отклонили."
            )
        except Exception:
            pass

        await callback.message.edit_reply_markup(
            reply_markup=None
        )
        await callback.answer("Заявка отклонена.")

    elif action == "reply":
        db.execute(
            "UPDATE submissions SET status = 'open' WHERE id = ?",
            (sub_id,)
        )
        db.commit()

        await callback.answer("Диалог открыт!")
        await callback.message.reply(
            "💬 Диалог открыт.\n"
            "Ответь через функцию Telegram «Ответить» "
            "на копию сообщения студента ниже. "
            "Твой ответ отправится ему от имени бота."
        )


@dp.message(F.chat.type == "supergroup")
async def admin_reply(message: Message):
    if message.chat.id != GROUP_ID:
        return

    if not message.from_user or not message.reply_to_message:
        return

    if not await is_admin(message.from_user.id):
        return

    replied_id = message.reply_to_message.message_id

    row = db.execute(
        "SELECT id, user_id FROM submissions "
        "WHERE admin_copy_id = ? "
        "ORDER BY id DESC LIMIT 1",
        (replied_id,)
    ).fetchone()

    if not row:
        return

    sub_id, user_id = row

    status_row = db.execute(
        "SELECT status FROM submissions WHERE id = ?",
        (sub_id,)
    ).fetchone()

    if not status_row or status_row[0] not in ("open", "pending"):
        await message.reply(
            "Этот диалог закрыт. Нажми «Ответить автору» "
            "в карточке заявки, чтобы открыть его снова."
        )
        return

    try:
        await bot.copy_message(
            chat_id=user_id,
            from_chat_id=message.chat.id,
            message_id=message.message_id
        )

        db.execute(
            "UPDATE submissions SET status = 'open' WHERE id = ?",
            (sub_id,)
        )
        db.commit()

        await message.reply("✅ Ответ отправлен студенту.")
    except Exception:
        await message.reply(
            "Не удалось отправить ответ. Возможно, студент "
            "не запускал бота или заблокировал его."
        )


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main()) 
