"""Ответ «печатается» на глазах: бот отправляет сообщение и редактирует его по мере генерации.
Telegram ограничивает частоту правок, поэтому обновляем не чаще раза в секунду."""

import asyncio
import contextlib
import time

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import LinkPreviewOptions, Message
from chatgpt_md_converter import split_html_for_telegram, telegram_format

LIMIT = 4000  # у Telegram 4096 символов на сообщение, оставляем запас
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)
INTERVAL = 1.0


class Streamer:
    def __init__(self, bot: Bot, chat_id: int, reply_to: int | None = None):
        self.bot, self.chat_id, self.reply_to = bot, chat_id, reply_to
        self.text, self.status = "", ""
        self.sent: list[Message] = []  # длинный ответ — несколько сообщений
        self.shown, self.last_edit = "", 0.0
        self._typing: asyncio.Task | None = None

    async def __aenter__(self):
        self._typing = asyncio.create_task(self._keep_typing())
        return self

    async def __aexit__(self, *exc):
        self._typing.cancel()

    async def _keep_typing(self) -> None:
        # «печатает…» гаснет через 5 секунд — обновляем, пока идёт ответ
        with contextlib.suppress(Exception):
            while True:
                await self.bot.send_chat_action(self.chat_id, "typing")
                await asyncio.sleep(4.5)

    async def add(self, delta: str) -> None:
        self.text += delta
        self.status = ""
        await self._flush()

    async def set_status(self, status: str) -> None:
        self.status = status
        await self._flush(force=True)

    async def _flush(self, force: bool = False) -> None:
        if not force and time.monotonic() - self.last_edit < INTERVAL:
            return
        # Во время генерации — простой текст: незакрытая разметка (**жирн…) сломала бы HTML
        view = self.text[-LIMIT:] if len(self.text) <= LIMIT else "…" + self.text[-LIMIT:]
        view = (view + ("\n\n" if view else "") + self.status).strip() or "…"
        if view == self.shown:
            return
        await self._edit(view, None)

    async def _edit(self, text: str, parse_mode: str | None, index: int = 0, plain: str = "") -> None:
        self.last_edit = time.monotonic()
        try:
            if index < len(self.sent):
                await self.sent[index].edit_text(text, parse_mode=parse_mode, link_preview_options=NO_PREVIEW)
            else:
                self.sent.append(
                    await self.bot.send_message(
                        self.chat_id,
                        text,
                        parse_mode=parse_mode,
                        reply_to_message_id=self.reply_to,
                        link_preview_options=NO_PREVIEW,
                    )
                )
            self.shown = text
        except TelegramRetryAfter as e:
            # Слишком часто правим: ждём, сколько просит Telegram, и повторяем (финальный вид важен)
            await asyncio.sleep(e.retry_after)
            await self._edit(text, parse_mode, index, plain)
        except TelegramBadRequest as e:
            if "not modified" not in str(e) and parse_mode:
                await self._edit(plain or text, None, index)  # разметка не прошла — показываем как есть

    async def finish(self, text: str | None = None) -> None:
        """Финальный вид: Markdown → HTML Telegram, длинный ответ — на несколько сообщений."""
        self.text = (text if text is not None else self.text).strip() or "…"
        chunks = split_html_for_telegram(telegram_format(self.text), max_length=LIMIT)
        for i, chunk in enumerate(chunks):
            await self._edit(chunk, "HTML", i, plain=self.text[i * LIMIT : (i + 1) * LIMIT])
