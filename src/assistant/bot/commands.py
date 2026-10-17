"""Команды и настройки: /start, /help, /new, /settings, /memory, /reminders."""

from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select, update

from assistant.agent.core import STYLES
from assistant.bot import confirm
from assistant.db import Session
from assistant.db.models import Reminder, User
from assistant.services import billing, conversation, memory, users

router = Router()

TIMEZONES = {  # города России по часовым поясам; любой другой — /tz Area/City
    "Калининград": "Europe/Kaliningrad",
    "Москва": "Europe/Moscow",
    "Самара": "Europe/Samara",
    "Екатеринбург": "Asia/Yekaterinburg",
    "Омск": "Asia/Omsk",
    "Новосибирск": "Asia/Novosibirsk",
    "Иркутск": "Asia/Irkutsk",
    "Якутск": "Asia/Yakutsk",
    "Владивосток": "Asia/Vladivostok",
    "Магадан": "Asia/Magadan",
    "Камчатка": "Asia/Kamchatka",
}
STYLE_NAMES = {"balanced": "⚖️ Обычный", "brief": "⚡ Кратко", "detailed": "📖 Подробно", "friendly": "😊 Дружески"}

WELCOME = """Привет, {name}! Я личный ИИ-ассистент 🤖

Просто пишите или присылайте голосовые. Я умею:
• отвечать на вопросы и помогать с текстами
• ⏰ напоминать: «напомни завтра в 9 позвонить маме»
• ✅ вести задачи и 📝 заметки
• 🧠 запоминать важное о вас: «запомни, я не ем мясо»
• 🌤 погода, 💱 курсы валют, 🧮 точные расчёты
• 📄 разбирать PDF и Word, 🖼 фото и скриншоты
• 🔎 искать в интернете

/settings — часовой пояс и стиль · /plan — тариф · /new — начать заново"""


@router.message(CommandStart())
async def start(message: Message) -> None:
    async with Session() as s, s.begin():
        user = await users.touch(s, message.from_user)
    await message.answer(WELCOME.format(name=user.first_name or "друг"))
    if user.timezone == "Europe/Moscow":
        await message.answer("Для точных напоминаний выберите свой часовой пояс:", reply_markup=_tz_kb())


@router.message(Command("help"))
async def help_(message: Message) -> None:
    await message.answer(WELCOME.format(name=message.from_user.first_name or "друг"))


@router.message(Command("new"))
async def new_chat(message: Message) -> None:
    async with Session() as s, s.begin():
        user = await users.touch(s, message.from_user)
        await conversation.reset(s, user)
    await message.answer("🆕 Начали с чистого листа. Память, заметки и напоминания на месте.")


# ---------- Настройки ----------


def _settings(user: User) -> tuple[str, object]:
    p = billing.plan(user)
    text = (
        f"⚙️ Настройки\n\nЧасовой пояс: {user.timezone}\nСтиль ответов: {STYLE_NAMES[user.style]}\n"
        f"Долгая память: {'включена' if user.memory_enabled else 'выключена'}\nТариф: {p.name}"
    )
    kb = InlineKeyboardBuilder()
    kb.button(text="🕐 Часовой пояс", callback_data="set:tz")
    kb.button(text="🎨 Стиль", callback_data="set:style")
    kb.button(text=f"🧠 Память: {'выкл' if user.memory_enabled else 'вкл'}", callback_data="set:memory")
    kb.button(text="🗑 Удалить мои данные", callback_data="set:wipe")
    kb.adjust(2, 1, 1)
    return text, kb.as_markup()


def _tz_kb():
    kb = InlineKeyboardBuilder()
    for city, tz in TIMEZONES.items():
        kb.button(text=city, callback_data=f"tz:{tz}")
    kb.adjust(3)
    return kb.as_markup()


@router.message(Command("settings"))
async def settings_cmd(message: Message) -> None:
    async with Session() as s, s.begin():
        user = await users.touch(s, message.from_user)
    text, kb = _settings(user)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("set:"))
async def settings_button(callback: CallbackQuery) -> None:
    action = callback.data.split(":", 1)[1]
    if action == "tz":
        await callback.message.edit_text(
            "Выберите город с вашим временем (или /tz Europe/Berlin):", reply_markup=_tz_kb()
        )
    elif action == "style":
        kb = InlineKeyboardBuilder()
        for code, name in STYLE_NAMES.items():
            kb.button(text=name, callback_data=f"style:{code}")
        kb.adjust(2)
        await callback.message.edit_text("Как мне отвечать?", reply_markup=kb.as_markup())
    elif action == "memory":
        async with Session() as s, s.begin():
            user = await s.get(User, callback.from_user.id)
            user.memory_enabled = not user.memory_enabled
        text, kb = _settings(user)
        await callback.message.edit_text(text, reply_markup=kb)
    elif action == "wipe":
        await confirm.ask(
            callback.bot,
            callback.from_user.id,
            "Удалить ВСЮ историю, память, заметки, задачи, напоминания и документы? Это необратимо.",
            "all_data",
            0,
        )
    await callback.answer()


async def _set(user_id: int, **values) -> User:
    async with Session() as s, s.begin():
        await s.execute(update(User).where(User.id == user_id).values(**values))
        return await s.get(User, user_id)


@router.callback_query(F.data.startswith("tz:"))
async def set_tz(callback: CallbackQuery) -> None:
    tz = callback.data.split(":", 1)[1]
    if tz not in TIMEZONES.values():
        await callback.answer("Неизвестный пояс", show_alert=True)
        return
    user = await _set(callback.from_user.id, timezone=tz)
    text, kb = _settings(user)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer(f"Часовой пояс: {tz}")


@router.message(Command("tz"))
async def set_tz_text(message: Message) -> None:
    from zoneinfo import ZoneInfo, available_timezones

    tz = (message.text.split(maxsplit=1) + [""])[1].strip()
    if tz not in available_timezones():
        await message.answer("Формат: /tz Europe/Berlin (названия из базы IANA)")
        return
    await _set(message.from_user.id, timezone=tz)
    await message.answer(f"🕐 Часовой пояс: {tz}, у вас сейчас {datetime.now(ZoneInfo(tz)):%H:%M}")


@router.callback_query(F.data.startswith("style:"))
async def set_style(callback: CallbackQuery) -> None:
    style = callback.data.split(":", 1)[1]
    if style not in STYLES:
        return
    user = await _set(callback.from_user.id, style=style)
    text, kb = _settings(user)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


# ---------- Что бот знает и о чём напомнит ----------


@router.message(Command("memory"))
async def show_memory(message: Message) -> None:
    async with Session() as s:
        facts = await memory.memories(s, message.from_user.id, limit=50)
    if not facts:
        await message.answer("🧠 Пока ничего не запомнил. Скажите, например: «запомни, что я живу в Петропавловске».")
        return
    lines = "\n".join(f"• {m.text}" for m in facts)
    await message.answer(f"🧠 Что я о вас помню:\n{lines}\n\nЧтобы забыть, напишите: «забудь, что …»")


@router.message(Command("reminders"))
async def show_reminders(message: Message) -> None:
    async with Session() as s, s.begin():
        user = await users.touch(s, message.from_user)
        rs = list(
            await s.scalars(
                select(Reminder)
                .where(Reminder.user_id == user.id, Reminder.active)
                .order_by(Reminder.fire_at)
                .limit(30)
            )
        )
    if not rs:
        await message.answer("⏰ Активных напоминаний нет. Скажите: «напомни через час выключить духовку».")
        return
    from zoneinfo import ZoneInfo

    kb = InlineKeyboardBuilder()
    lines = []
    for r in rs:
        when = r.fire_at.astimezone(ZoneInfo(user.timezone))
        lines.append(f"• {when:%d.%m %H:%M} — {r.text}{' 🔁' if r.repeat != 'none' else ''}")
        kb.button(text=f"✖️ {r.text[:25]}", callback_data=f"rmcancel:{r.id}")
    kb.adjust(1)
    await message.answer("⏰ Напоминания:\n" + "\n".join(lines), reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("rmcancel:"))
async def cancel_reminder(callback: CallbackQuery) -> None:
    async with Session() as s, s.begin():
        r = await s.execute(
            update(Reminder)
            .where(Reminder.id == int(callback.data.split(":")[1]), Reminder.user_id == callback.from_user.id)
            .values(active=False)
        )
    await callback.answer("Отменено" if r.rowcount else "Уже отменено")
