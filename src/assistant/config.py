from pathlib import Path
from typing import Annotated, Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Настройки из переменных окружения и .env. Имена без учёта регистра: BOT_TOKEN → bot_token."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bot_token: SecretStr
    database_url: str
    redis_url: str = ""  # пусто — состояния диалогов в памяти (для разработки)
    admin_ids: Annotated[set[int], NoDecode] = set()

    # --- Модели. Провайдер выбирается здесь, код агента от него не зависит ---
    llm_provider: Literal["gemini", "anthropic", "openai"] = "gemini"
    model_fast: str = "gemini-flash-latest"  # бесплатный тариф, сжатие истории
    model_smart: str = "gemini-pro-latest"  # Pro
    embed_provider: Literal["gemini", "openai"] = "gemini"
    embed_model: str = "gemini-embedding-001"
    stt_provider: Literal["gemini", "openai"] = "gemini"  # расшифровка голосовых
    gemini_api_key: SecretStr = SecretStr("")
    anthropic_api_key: SecretStr = SecretStr("")
    openai_api_key: SecretStr = SecretStr("")
    openai_base_url: str = ""  # OpenRouter, DeepSeek, Ollama: http://localhost:11434/v1
    # Цена моделей, $ за 1 млн токенов (вход/выход) — для учёта себестоимости
    price_fast: tuple[float, float] = (0.3, 2.5)
    price_smart: tuple[float, float] = (1.25, 10.0)

    # --- Инструменты ---
    tavily_api_key: SecretStr = SecretStr("")  # поиск в интернете; пусто — инструмент выключен

    # --- Тарифы ---
    free_messages: int = 20  # в сутки
    free_files: int = 3
    pro_messages: int = 300
    pro_files: int = 50
    pro_days: int = 30
    pro_price_stars: int = 250
    pro_price_rub: int = 399
    payment_token: SecretStr = SecretStr("")  # провайдер карт из @BotFather (ЮKassa); пусто — только Stars

    default_timezone: str = "Europe/Moscow"
    webhook_url: str = ""  # задан — вебхук, иначе long polling
    webhook_secret: SecretStr = SecretStr("")
    polling: bool = True
    host: str = "127.0.0.1"
    port: int = 8090

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _split_ids(cls, v):
        return {int(x) for x in v.split(",") if x.strip()} if isinstance(v, str) else v

    @field_validator("database_url")
    @classmethod
    def _async_driver(cls, v: str) -> str:
        return v.replace("postgresql://", "postgresql+asyncpg://", 1) if v.startswith("postgresql://") else v


settings = Settings()
