from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.base import BaseStorage
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from assistant.config import settings

COMMANDS = [
    BotCommand(command="new", description="Начать разговор заново"),
    BotCommand(command="reminders", description="Мои напоминания"),
    BotCommand(command="memory", description="Что я о вас помню"),
    BotCommand(command="settings", description="Часовой пояс, стиль, данные"),
    BotCommand(command="plan", description="Тариф и Pro"),
    BotCommand(command="help", description="Что я умею"),
]


def storage() -> BaseStorage:
    if settings.redis_url:
        from aiogram.fsm.storage.redis import RedisStorage

        return RedisStorage.from_url(settings.redis_url)
    return MemoryStorage()


def create_bot() -> Bot:
    return Bot(settings.bot_token.get_secret_value())


def create_dispatcher() -> Dispatcher:
    from assistant.bot import admin, chat, commands, confirm, payments

    dp = Dispatcher(storage=storage())
    # Админка первой (рассылка ловит любое сообщение), чат последним (ловит весь текст)
    dp.include_routers(admin.router, payments.router, confirm.router, commands.router, chat.router)
    return dp
