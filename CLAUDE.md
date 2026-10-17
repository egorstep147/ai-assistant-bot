# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Что это

Личный ИИ-ассистент в Telegram: агентный цикл с инструментами (память, заметки, задачи, напоминания,
интернет, погода, курсы, калькулятор, документы), голос/фото/файлы, тарифы Free/Pro с оплатой Stars и картой.

## Tech stack

- Python 3.13, uv, ruff, pytest (+pytest-asyncio)
- aiogram 3 + FastAPI в одном процессе (webhook или polling), uvicorn
- PostgreSQL + pgvector через SQLAlchemy 2 async (asyncpg) + Alembic; Redis — FSM aiogram
- Модели: google-genai, anthropic, openai SDK за общим интерфейсом `llm/base.py`

## Commands

- `docker compose up -d` — PostgreSQL (pgvector, порт 5433) и Redis (6380)
- `uv run assistant migrate` / `uv run assistant` — миграции / запуск
- `uv run pytest` — тесты (база assistant_test в том же Postgres или TEST_DATABASE_URL)
- `uv run ruff check src tests && uv run ruff format src tests`
- `uv run alembic revision --autogenerate -m "..."`; в шаблоне миграций уже импортирован pgvector
- Деплой: `deploy/DEPLOY.md`

## Architecture

- `agent/core.py` — цикл: модель → tool_calls → `registry.call` → результат в историю → …, максимум MAX_STEPS;
  на последнем шаге инструменты не передаются. События `Delta` (кусок текста) и `Done`
- `agent/registry.py` — `@tool(name, description, status=, enabled=, **json_schema_props)`; `Ctx` — пользователь,
  статус-колбэк, confirm-колбэк. Инструменты ничего не удаляют сами — только `ctx.confirm` (кнопки в `bot/confirm.py`)
- `llm/` — `LLM._stream` отдаёт str-дельты и последним `Reply`; ретраи до первого токена в `LLM.stream`.
  Gemini: ответ модели внутри хода передаётся обратно как `raw` (thought signatures)
- `services/billing.py` — план по `pro_until`, суточные лимиты и себестоимость в `usage`
- `services/conversation.py` — последние KEEP сообщений + `user.summary`, сжатие в фоне
- `bot/chat.py` — общий путь `answer()`: пользователь → лимиты → агент со `Streamer` → история и учёт
- Тесты: `tests/fakes.py` — FakeLLM (сценарий), FakeEmbedder, FakeSession (aiogram без сети)

## Rules

- Все ответы и комментарии в коде на русском
- Код компактный, без лишних абстракций
- Всё асинхронно; HTTP — `httpx.AsyncClient`
- Секреты только через `.env`, не хардкодить и не логировать
- Зависимости только через `uv add`
- Перед изменением нескольких файлов — сначала показать план
- После изменений объяснять, что и зачем сделано (я учусь)
