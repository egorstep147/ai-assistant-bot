"""Тесты работают с отдельной базой (TEST_DATABASE_URL или assistant_test в docker compose),
поддельной моделью и Telegram без сети. Настройки задаём до импорта assistant."""

import os
from pathlib import Path

import pytest


def _local_test_db() -> str:
    env = Path(__file__).parents[1] / ".env"
    lines = env.read_text().splitlines() if env.exists() else []
    pw = next((ln.split("=", 1)[1].strip() for ln in lines if ln.startswith("POSTGRES_PASSWORD=")), "assistant")
    return f"postgresql://assistant:{pw}@localhost:5433/assistant_test"


os.environ |= {
    "BOT_TOKEN": "123456:TEST-TOKEN",
    "DATABASE_URL": os.environ.get("TEST_DATABASE_URL") or _local_test_db(),
    "ADMIN_IDS": "1",
    "POLLING": "false",
    "REDIS_URL": "",
    "TAVILY_API_KEY": "",
    "PAYMENT_TOKEN": "TEST:card",
    "FREE_MESSAGES": "3",
}

from sqlalchemy import text  # noqa: E402

from assistant.db import engine  # noqa: E402
from assistant.db.models import Base  # noqa: E402
from tests.fakes import FakeEmbedder, FakeLLM  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
async def database():
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    # Соединения пула открылись до создания расширения — пересоздаём, чтобы тип vector зарегистрировался
    await engine.dispose()
    yield
    await engine.dispose()


@pytest.fixture(autouse=True)
def fake_embedder(monkeypatch):
    emb = FakeEmbedder()
    monkeypatch.setattr("assistant.services.memory.get_embedder", lambda: emb)
    return emb


@pytest.fixture
def llm(monkeypatch):
    """llm.script = [...] — что «ответит» модель."""
    fake = FakeLLM([])
    for target in (
        "assistant.agent.core.get_llm",
        "assistant.services.conversation.get_llm",
        "assistant.bot.chat.get_stt",
    ):
        monkeypatch.setattr(target, lambda: fake)
    return fake
