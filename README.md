# AI Assistant Bot — личный ИИ-ассистент в Telegram

Агент, который не только отвечает, но и **делает**: ставит напоминания, ведёт задачи и заметки,
запоминает важное о пользователе, ищет в интернете, читает документы и голосовые.
Готов к продаже: тарифы Free/Pro, подписка в Telegram Stars и оплата картой, учёт себестоимости.

[![CI](https://github.com/egorstep147/ai-assistant-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/egorstep147/ai-assistant-bot/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.13-blue) ![License](https://img.shields.io/badge/license-MIT-green)

```
👤 Напоминай по будням в 9 про пару и запомни, что я учусь в КамГУ
🤖 ⏰ Ставлю напоминание… 🧠 Запоминаю…
   Готово! Каждый будний день в 9:00 напомню про пару. И запомнил, что вы учитесь в КамГУ.
```

## Что умеет

| | |
|---|---|
| 💬 Диалог | Ответ печатается на глазах (стриминг), длинная история сжимается автоматически |
| ⏰ Напоминания | «через час», «каждый понедельник в 10» — в часовом поясе пользователя, с повторами |
| ✅ Задачи и 📝 заметки | Список дел со сроками, заметки с поиском **по смыслу** |
| 🧠 Долгая память | «Запомни, что я не ем мясо» — факт попадает в контекст будущих разговоров |
| 🔎 Интернет | Поиск (Tavily) и чтение страниц |
| 🌤 💱 🧮 | Погода (Open-Meteo), курсы ЦБ РФ, точный калькулятор |
| 🎤 🖼 📄 | Голосовые, фото и скриншоты, PDF/DOCX/TXT; документ можно сохранить в личную базу и спрашивать по нему потом |
| 💎 Монетизация | Free/Pro с дневными лимитами, подписка Stars, оплата картой (ЮKassa), возвраты |
| 🛠 Админка | Пользователи, активность, токены, расходы в $, выручка; выдача Pro, бан, рассылка |

## Как устроен агент

```
сообщение ─► контекст: системный промпт + память + сжатая история + последние реплики
             │
             ▼
        ┌─ модель ──► текст ──────────────────────────────► ответ (стриминг в Telegram)
        │    │
        │    └─► вызов инструмента (remember, set_reminder, web_search…)
        │              │
        └──── результат инструмента ◄─┘        не больше 8 шагов
```

Цикл написан без фреймворков (`src/assistant/agent/core.py`, ~80 строк), чтобы было видно каждый шаг.
Инструмент — обычная async-функция с декоратором `@tool`: описание и JSON Schema аргументов модель видит
и сама решает, когда его вызвать (`agent/toolbox/`).

**Любая модель.** Адаптеры приводят Gemini, Claude и OpenAI-совместимые API (OpenRouter, DeepSeek, Ollama)
к одному интерфейсу (`llm/base.py`): стриминг текста + вызовы инструментов + учёт токенов. Провайдер — одна
строка в `.env`.

**Память по смыслу.** Факты, заметки и документы хранятся с векторами (pgvector, HNSW-индекс):
«какая у меня аллергия?» находит «Аллергия на орехи», хотя общих слов почти нет.

**Безопасность.**
- Текст страниц и документов модель получает как данные с пометкой «не инструкции» (защита от prompt injection).
- Удаление — только по кнопке подтверждения: агент может попросить, но не стереть сам.
- `read_url` не ходит во внутреннюю сеть и на метаданные облака (защита от SSRF), проверяя и редиректы.
- Калькулятор разбирает выражение деревом, а не `eval`.
- Цены и лимиты проверяет сервер; платёж сверяется с прайсом, повтор апдейта не продлит Pro дважды.

## Стек

aiogram 3 · FastAPI · SQLAlchemy 2 (async) + Alembic · PostgreSQL + pgvector · Redis ·
google-genai / anthropic / openai SDK · pytest · Docker · GitHub Actions

```
src/assistant/
├── agent/        # core.py — агентный цикл; registry.py — реестр инструментов; toolbox/ — инструменты
├── llm/          # base.py — общий интерфейс; gemini.py, anthropic.py, openai.py — адаптеры
├── services/     # billing (тарифы, учёт), conversation (история, сжатие), memory (векторный поиск),
│                 # reminders (планировщик), files (PDF/DOCX), users
├── bot/          # chat (текст/голос/фото/файлы), streaming, commands, payments, admin, confirm
├── db/models.py  # схема базы
└── app.py        # FastAPI (вебхук) + запуск бота + фоновые напоминания
tests/            # 39 тестов: поддельная модель и Telegram без сети, настоящий PostgreSQL
```

## Быстрый старт

```sh
cp .env.example .env     # BOT_TOKEN, GEMINI_API_KEY (бесплатно: aistudio.google.com), POSTGRES_PASSWORD, DATABASE_URL
docker compose up -d     # PostgreSQL с pgvector и Redis
uv sync
uv run assistant migrate
uv run assistant         # бот (polling) + http://127.0.0.1:8090/health
```
Целиком в Docker: `docker compose --profile app up -d --build`. Деплой на VPS: [deploy/DEPLOY.md](deploy/DEPLOY.md).

## Новый инструмент за 5 минут

```python
@tool(
    "convert_units",
    "Перевести единицы измерения",
    status="📏 Считаю…",
    value={"type": "number"},
    unit_from={"type": "string"},
    unit_to={"type": "string"},
)
async def convert_units(ctx: Ctx, value: float, unit_from: str, unit_to: str) -> str: ...
```
Положите в `agent/toolbox/`, импортируйте в `toolbox/__init__.py` — агент начнёт им пользоваться.

## Разработка

```sh
uv run pytest
uv run ruff check src tests && uv run ruff format src tests
uv run alembic revision --autogenerate -m "что изменилось"
```

## Лицензия
MIT
