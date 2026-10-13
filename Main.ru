import os
import asyncio
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup,
    InlineKeyboardButton
)
from aiogram.filters import CommandStart
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
ADMIN_GROUP_ID = os.getenv("ADMIN_GROUP_ID")

bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()

pending = {}


def moderation_buttons():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Одобрить", callback_data="approve"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data="reject")
        ]
    ])


@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "Привет! 👋 Это подслушка колледжа.\n\n"
        "Отправь сюда текст, фото, видео или голосовое сообщение. "
        "Перед публикацией всё проверят администраторы."
    )


@dp.message()
async def receive(message: Message):
    if not ADMIN_GROUP_ID:
        return

    try:
        group_id = int(ADMIN_GROUP_ID)
    except ValueError:
        return

    user = message.from_user
    if not user or message.chat.type != "private":
        return

    # Сохраняем оригинал для публикации после одобрения
    pending_key = f"{message.chat.id}:{message.message_id}"
    pending[pending_key] = {
        "chat_id": message.chat.id,
        "message_id": message.message_id,
        "user_id": user.id,
        "username": user.username or "нет",
        "name": user.full_name,
    }

    caption = (
        "📩 <b>Новая предложка</b>\n"
        f"👤 Имя: {user.full_name}\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"🔗 Username: @{user.username if user.username else 'нет'}\n"
        f"🔑 Ключ: <code>{pending_key}</code>"
    )

    try:
        await bot.send_message(group_id, caption)
        await bot.copy_message(
            chat_id=group_id,
            from_chat_id=message.chat.id,
            message_id=message.message_id
        )
        await bot.send_message(
            group_id,
            f"Выбери действие для ключа <code>{pending_key}</code>:",
            reply_markup=moderation_buttons()
        )
        await message.answer("✅ Предложка отправлена на проверку!")
    except Exception:
        await message.answer(
            "Не получилось отправить предложку. Попробуй позже."
        )


@dp.callback_query(F.data.in_({"approve", "reject"}))
async def moderate(callback: CallbackQuery):
    if not callback.message:
        await callback.answer()
        return

    # Только участники закрытой группы могут нажимать кнопки.
    if callback.message.chat.id != int(ADMIN_GROUP_ID):
        await callback.answer("Нет доступа", show_alert=True)
        return

    # Находим ключ в сообщении с кнопками
    text = callback.message.text or ""
    if "Ключ:" not in text:
        await callback.answer(
            "Не найден ключ заявки. Обратись к разработчику.",
            show_alert=True
        )
        return

    key = text.split("Ключ:")[-1].strip().replace("</code>", "").replace("<code>", "")
    item = pending.get(key)

    if not item:
        await callback.answer(
            "Заявка не найдена или бот перезапускался.",
            show_alert=True
        )
        return

    if callback.data == "approve":
        await bot.copy_message(
            chat_id=CHANNEL_ID,
            from_chat_id=item["chat_id"],
            message_id=item["message_id"]
        )
        await bot.send_message(
            item["chat_id"],
            "✅ Твою предложку одобрили и опубликовали!"
        )
        await callback.message.edit_text("✅ Опубликовано")
    else:
        await bot.send_message(
            item["chat_id"],
            "❌ Твою предложку отклонили."
        )
        await callback.message.edit_text("❌ Отклонено")

    pending.pop(key, None)
    await callback.answer()


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
