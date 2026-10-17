"""Тарифы, лимиты, учёт расхода токенов и продление Pro."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from assistant.config import settings
from assistant.db import AsyncSession
from assistant.db.models import Payment, Usage, User
from assistant.llm import Usage as Tokens


@dataclass
class Plan:
    name: str
    model: str
    messages: int  # в сутки
    files: int
    price: tuple[float, float]  # $ за 1 млн токенов: вход, выход


def plan(user: User) -> Plan:
    if user.is_pro(datetime.now(UTC)):
        return Plan("Pro", settings.model_smart, settings.pro_messages, settings.pro_files, settings.price_smart)
    return Plan("Free", settings.model_fast, settings.free_messages, settings.free_files, settings.price_fast)


def today() -> date:
    return datetime.now(UTC).date()  # сутки лимитов — по UTC, одинаково для всех


async def usage_today(s: AsyncSession, user_id: int) -> Usage:
    return await s.get(Usage, (user_id, today())) or Usage(user_id=user_id, day=today(), messages=0, files=0)


async def check(s: AsyncSession, user: User, files: int = 0) -> str | None:
    """None — можно отвечать, иначе текст для пользователя, почему нельзя."""
    p, u = plan(user), await usage_today(s, user.id)
    if u.messages >= p.messages:
        more = "" if p.name == "Pro" else " Оформите Pro — /plan"
        return f"Лимит на сегодня исчерпан ({p.messages} сообщений).{more}"
    if files and u.files + files > p.files:
        return f"Лимит файлов на сегодня — {p.files}." + ("" if p.name == "Pro" else " Больше — в Pro: /plan")
    return None


async def record(s: AsyncSession, user: User, tokens: Tokens, files: int = 0, *, count_message: bool = True) -> None:
    """Upsert строки расхода за сегодня: увеличиваем счётчики одним запросом."""
    p = plan(user)
    cost = round(tokens.input * p.price[0] + tokens.output * p.price[1])  # $/1M токенов × токены = микродоллары
    values = dict(
        messages=int(count_message),
        files=files,
        input_tokens=tokens.input,
        output_tokens=tokens.output,
        cost_micro=cost,
    )
    stmt = insert(Usage).values(user_id=user.id, day=today(), **values)
    await s.execute(
        stmt.on_conflict_do_update(
            index_elements=["user_id", "day"],
            set_={k: getattr(Usage, k) + getattr(stmt.excluded, k) for k in values},
        )
    )


async def extend_pro(s: AsyncSession, user: User, *, until: datetime | None = None, days: int = 0) -> datetime:
    """Продлить Pro до даты (подписка Stars сообщает её сама) или на N дней от текущего конца."""
    now = datetime.now(UTC)
    base = user.pro_until if user.pro_until and user.pro_until > now else now
    user.pro_until = max(base, until) if until else base + timedelta(days=days)
    return user.pro_until


async def stats(s: AsyncSession, days: int) -> dict:
    since = today() - timedelta(days=days - 1)
    row = (
        await s.execute(
            select(
                func.count(func.distinct(Usage.user_id)),
                func.coalesce(func.sum(Usage.messages), 0),
                func.coalesce(func.sum(Usage.input_tokens + Usage.output_tokens), 0),
                func.coalesce(func.sum(Usage.cost_micro), 0),
            ).where(Usage.day >= since)
        )
    ).one()
    paid = (
        await s.execute(
            select(Payment.currency, func.sum(Payment.amount))
            .where(Payment.created_at >= datetime.combine(since, datetime.min.time(), UTC), Payment.refunded.is_(False))
            .group_by(Payment.currency)
        )
    ).all()
    return {
        "active": int(row[0]),
        "messages": int(row[1]),
        "tokens": int(row[2]),
        "cost_usd": int(row[3]) / 1e6,  # SUM в Postgres возвращает Decimal
        "revenue": {cur: int(amount) for cur, amount in paid},
    }
