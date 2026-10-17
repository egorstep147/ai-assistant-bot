"""Приложение: бот (polling или webhook) + FastAPI (вебхук, здоровье) + фоновый планировщик напоминаний."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from aiogram import Bot, Dispatcher
from aiogram.types import Update
from fastapi import FastAPI, Header, HTTPException, Request

from assistant.bot import COMMANDS, create_bot, create_dispatcher
from assistant.config import settings
from assistant.db import engine
from assistant.services import reminders

log = logging.getLogger(__name__)
WEBHOOK_PATH = "/tg/webhook"
_tasks: set[asyncio.Task] = set()  # ссылки на фоновые обработки апдейтов


async def send_safe(bot: Bot, chat_id: int, text: str) -> bool:
    try:
        await bot.send_message(chat_id, text)
        return True
    except Exception as e:  # noqa: BLE001 — заблокировал бота и т.п.: остальные напоминания уходят дальше
        log.info("Напоминание для %s не доставлено: %s", chat_id, e)
        return False


async def reminders_loop(bot: Bot) -> None:
    while True:
        try:
            await reminders.fire_due(lambda uid, text: send_safe(bot, uid, text))
        except Exception:
            log.exception("Ошибка планировщика напоминаний")
        await asyncio.sleep(20)


async def setup_telegram(bot: Bot, dp: Dispatcher) -> asyncio.Task | None:
    if not settings.webhook_url and not settings.polling:
        return None
    await bot.set_my_commands(COMMANDS)
    if settings.webhook_url:
        if not settings.webhook_secret.get_secret_value():
            raise RuntimeError("Для вебхука задайте WEBHOOK_SECRET")
        await bot.set_webhook(
            settings.webhook_url.rstrip("/") + WEBHOOK_PATH,
            secret_token=settings.webhook_secret.get_secret_value(),
            allowed_updates=dp.resolve_used_update_types(),
        )
        return None
    await bot.delete_webhook()
    return asyncio.create_task(dp.start_polling(bot, handle_signals=False))


def create_app() -> FastAPI:
    bot, dp = create_bot(), create_dispatcher()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.bot = bot
        tasks = [asyncio.create_task(reminders_loop(bot))]
        if polling := await setup_telegram(bot, dp):
            tasks.append(polling)
        log.info("Ассистент запущен: %s / %s", settings.llm_provider, settings.model_fast)
        yield
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t
        await bot.session.close()
        await engine.dispose()

    app = FastAPI(title="AI Assistant", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.post(WEBHOOK_PATH)
    async def webhook(request: Request, x_telegram_bot_api_secret_token: str = Header("")) -> dict:
        if x_telegram_bot_api_secret_token != settings.webhook_secret.get_secret_value():
            raise HTTPException(403)
        update = Update.model_validate(await request.json(), context={"bot": bot})
        # Отвечаем Telegram сразу, а обработку (ответ модели — это секунды) ведём в фоне:
        # иначе Telegram посчитает вебхук зависшим и пришлёт апдейт повторно
        task = asyncio.create_task(dp.feed_update(bot, update))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
        return {"ok": True}

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True}

    return app
