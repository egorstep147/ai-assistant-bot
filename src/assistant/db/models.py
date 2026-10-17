"""Схема базы. Векторы (embedding) — смысл текста в виде 768 чисел: близкие по смыслу тексты дают
близкие векторы, поэтому «что я говорил про аллергию?» найдёт «у меня аллергия на орехи»."""

from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Index, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

EMBED_DIM = 768


def hnsw(table: str) -> Index:
    """HNSW-индекс: поиск ближайших по смыслу векторов без перебора всей таблицы."""
    return Index(
        f"ix_{table}_embedding", "embedding", postgresql_using="hnsw", postgresql_ops={"embedding": "vector_cosine_ops"}
    )


class Base(DeclarativeBase):
    type_annotation_map = {datetime: DateTime(timezone=True), date: Date, str: Text}


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # Telegram ID
    first_name: Mapped[str] = mapped_column(default="")
    username: Mapped[str | None]
    timezone: Mapped[str] = mapped_column(default="Europe/Moscow")
    style: Mapped[str] = mapped_column(default="balanced")  # balanced | brief | detailed | friendly
    memory_enabled: Mapped[bool] = mapped_column(default=True)
    summary: Mapped[str] = mapped_column(default="")  # краткое содержание старой части диалога
    summary_upto: Mapped[int] = mapped_column(default=0)  # id последнего сообщения, вошедшего в summary
    pro_until: Mapped[datetime | None]
    banned: Mapped[bool] = mapped_column(default=False)
    blocked: Mapped[bool] = mapped_column(default=False)  # пользователь заблокировал бота
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_seen: Mapped[datetime] = mapped_column(server_default=func.now())

    def is_pro(self, now: datetime) -> bool:
        return self.pro_until is not None and self.pro_until > now


class Message(Base):
    """История диалога: только реплики пользователя и итоговые ответы (без промежуточных вызовов)."""

    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_user_id_id", "user_id", "id"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[str]  # user | assistant
    content: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Memory(Base):
    """Долгая память: факты о пользователе, которые агент сохранил по просьбе или сам."""

    __tablename__ = "memories"
    __table_args__ = (hnsw("memories"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    text: Mapped[str]
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Note(Base):
    __tablename__ = "notes"
    __table_args__ = (hnsw("notes"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str]
    text: Mapped[str]
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    text: Mapped[str]
    due: Mapped[date | None]
    done: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Reminder(Base):
    __tablename__ = "reminders"
    __table_args__ = (Index("ix_reminders_due", "active", "fire_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    text: Mapped[str]
    fire_at: Mapped[datetime]
    repeat: Mapped[str] = mapped_column(default="none")  # none | daily | weekdays | weekly | monthly
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Document(Base):
    """Документ в личной базе знаний: текст режется на куски, у каждого куска свой вектор."""

    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str]
    chars: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (hnsw("chunks"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    idx: Mapped[int]
    text: Mapped[str]
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBED_DIM))


class Usage(Base):
    """Расход за сутки: лимиты тарифа и себестоимость в долларах (микродоллары, целым числом)."""

    __tablename__ = "usage"
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    day: Mapped[date] = mapped_column(primary_key=True)
    messages: Mapped[int] = mapped_column(default=0)
    files: Mapped[int] = mapped_column(default=0)
    input_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cost_micro: Mapped[int] = mapped_column(BigInteger, default=0)


class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    provider: Mapped[str]  # stars | card
    amount: Mapped[int]  # звёзды или рубли
    currency: Mapped[str]
    charge_id: Mapped[str] = mapped_column(unique=True)  # telegram_payment_charge_id: повтор апдейта не задвоит
    provider_charge_id: Mapped[str] = mapped_column(default="")
    pro_until: Mapped[datetime]
    refunded: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
