"""Админка: /admin (статистика), /grant, /ban, /unban, /refund, /broadcast."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select

from assistant.bot.payments import refund
from assistant.config import settings
from assistant.db import Session
from assistant.db.models import User
from assistant.services import billing

log = logging.getLogger(__name__)
router = Router()
router.message.filter(F.from_user.id.in_(settings.admin_ids))
router.callback_query.filter(F.from_user.id.in_(settings.admin_ids))
_tasks: set[asyncio.Task] = set()


class Broadcast(StatesGroup):
    text = State()


@router.message(Command("admin"))
async def admin(message: Message) -> None:
    now = datetime.now(UTC)
    async with Session() as s:
        total = await s.scalar(select(func.count()).select_from(User))
        pro = await s.scalar(select(func.count()).where(User.pro_until > now))
        day, month = await billing.stats(s, 1), await billing.stats(s, 30)

    def rev(st: dict) -> str:
        return ", ".join(f"{v} {'⭐' if k == 'XTR' else '₽'}" for k, v in st["revenue"].items()) or "0"

    await message.answer(
        f"📊 Пользователей: {total}, Pro: {pro}\n\n"
        f"Сегодня: активных {day['active']}, сообщений {day['messages']}, токенов {day['tokens']:,}, "
        f"расход ${day['cost_usd']:.2f}, выручка {rev(day)}\n"
        f"30 дней: активных {month['active']}, сообщений {month['messages']}, токенов {month['tokens']:,}, "
        f"расход ${month['cost_usd']:.2f}, выручка {rev(month)}\n\n"
        "/grant id дни · /ban id · /unban id · /refund charge_id · /broadcast".replace(",", " ")
    )


@router.message(Command("grant"))
async def grant(message: Message, command: CommandObject) -> None:
    try:
        user_id, days = map(int, (command.args or "").split())
    except ValueError:
        await message.answer("Формат: /grant 123456789 30")
        return
    async with Session() as s, s.begin():
        user = await s.get(User, user_id)
        if user is None:
            await message.answer("Пользователь не найден (он должен хоть раз написать боту)")
            return
        until = await billing.extend_pro(s, user, days=days)
    await message.answer(f"Pro для {user_id} до {until:%d.%m.%Y}")


async def _set_ban(message: Message, command: CommandObject, banned: bool) -> None:
    if not (command.args or "").strip().isdigit():
        await message.answer("Формат: /ban 123456789")
        return
    async with Session() as s, s.begin():
        user = await s.get(User, int(command.args))
        if user:
            user.banned = banned
    await message.answer(("Заблокирован" if banned else "Разблокирован") if user else "Не найден")


@router.message(Command("ban"))
async def ban(message: Message, command: CommandObject) -> None:
    await _set_ban(message, command, True)


@router.message(Command("unban"))
async def unban(message: Message, command: CommandObject) -> None:
    await _set_ban(message, command, False)


@router.message(Command("refund"))
async def refund_cmd(message: Message, command: CommandObject, bot: Bot) -> None:
    await message.answer(await refund(bot, (command.args or "").strip()))


@router.message(Command("broadcast"))
async def broadcast(message: Message, state: FSMContext) -> None:
    await state.set_state(Broadcast.text)
    await message.answer("Пришлите сообщение для рассылки. /cancel — отмена")


@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменено")


@router.message(Broadcast.text)
async def broadcast_preview(message: Message, state: FSMContext) -> None:
    await state.update_data(chat_id=message.chat.id, message_id=message.message_id)
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Отправить всем", callback_data="bc:go")
    kb.button(text="✖️ Отмена", callback_data="bc:no")
    await message.answer("Разослать это сообщение?", reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("bc:"))
async def broadcast_go(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    await state.clear()
    if callback.data == "bc:no" or not data:
        await callback.message.edit_text("Рассылка отменена")
        return
    await callback.message.edit_text("📣 Рассылка запущена")
    task = asyncio.create_task(_send_all(bot, callback.from_user.id, data["chat_id"], data["message_id"]))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _send_all(bot: Bot, admin_id: int, chat_id: int, message_id: int) -> None:
    since = datetime.now(UTC) - timedelta(days=180)
    async with Session() as s:
        ids = list(
            await s.scalars(
                select(User.id).where(User.blocked.is_(False), User.banned.is_(False), User.last_seen > since)
            )
        )
    ok = 0
    for uid in ids:
        try:
            await bot.copy_message(uid, chat_id, message_id)
            ok += 1
        except Exception as e:  # noqa: BLE001 — один недоступный не останавливает рассылку
            log.info("Рассылка: %s недоступен (%s)", uid, e)
        await asyncio.sleep(0.05)  # ≈20 в секунду, ниже лимита Telegram
    await bot.send_message(admin_id, f"📣 Доставлено {ok} из {len(ids)}")
