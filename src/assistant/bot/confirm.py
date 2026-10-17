"""Подтверждение удаления. Агент сам ничего не стирает: он просит, а бот показывает кнопки «Да/Нет».
Так случайная фраза или подсунутая со страницы «инструкция» не удалит данные пользователя."""

import secrets
import time

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import delete

from assistant.db import Session
from assistant.db.models import Note
from assistant.services import memory, users

router = Router()
TTL = 3600
PENDING: dict[str, tuple[int, str, int, float]] = {}  # токен → (user_id, действие, id, когда истекает)


async def ask(bot: Bot, user_id: int, question: str, action: str, obj_id: int) -> None:
    for t in [t for t, p in PENDING.items() if p[3] < time.time()]:
        PENDING.pop(t)  # чистим просроченные
    token = secrets.token_urlsafe(8)
    PENDING[token] = (user_id, action, obj_id, time.time() + TTL)
    kb = InlineKeyboardBuilder()
    kb.button(text="🗑 Да, удалить", callback_data=f"cf:y:{token}")
    kb.button(text="Отмена", callback_data=f"cf:n:{token}")
    await bot.send_message(user_id, f"❓ {question}", reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("cf:"))
async def answer(callback: CallbackQuery) -> None:
    _, choice, token = callback.data.split(":", 2)
    pending = PENDING.get(token)
    if pending is None or pending[0] != callback.from_user.id or pending[3] < time.time():
        await callback.answer("Запрос устарел", show_alert=True)
        return
    PENDING.pop(token)  # одноразовый: удаляем только после проверки, что жмёт владелец
    user_id, action, obj_id, _ = pending
    if choice == "n":
        await callback.message.edit_text("Отменено, ничего не удалено.")
        return
    async with Session() as s, s.begin():
        if action == "memory":
            ok = await memory.delete_memory(s, user_id, obj_id)
        elif action == "note":
            ok = (await s.execute(delete(Note).where(Note.id == obj_id, Note.user_id == user_id))).rowcount > 0
        elif action == "all_data":
            await users.delete_all_data(s, user_id)
            ok = True
        else:
            ok = False
    await callback.message.edit_text("✅ Удалено." if ok else "Уже удалено или не найдено.")
