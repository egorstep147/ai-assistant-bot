"""Напоминания: расчёт следующего срабатывания и фоновая рассылка."""

import calendar
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from assistant.db import AsyncSession, Session
from assistant.db.models import Reminder, User

log = logging.getLogger(__name__)
REPEATS = ("none", "daily", "weekdays", "weekly", "monthly")
MAX_ACTIVE = 100


def next_time(r: Reminder, tz: ZoneInfo, now: datetime) -> datetime | None:
    """Когда сработать в следующий раз. Считаем в часовом поясе пользователя: «каждый день в 9:00»
    остаётся в 9:00 и после перехода на летнее время."""
    t = r.fire_at.astimezone(tz)
    while t <= now:
        if r.repeat == "daily":
            t += timedelta(days=1)
        elif r.repeat == "weekdays":
            t += timedelta(days=3 if t.weekday() == 4 else 2 if t.weekday() == 5 else 1)
        elif r.repeat == "weekly":
            t += timedelta(weeks=1)
        elif r.repeat == "monthly":
            month = t.month % 12 + 1
            year = t.year + (t.month == 12)
            t = t.replace(year=year, month=month, day=min(t.day, calendar.monthrange(year, month)[1]))
        else:
            return None
        t = t.replace(tzinfo=None).replace(tzinfo=tz)  # пересчёт смещения пояса на новую дату
    return t


async def count_active(s: AsyncSession, user_id: int) -> int:
    return await s.scalar(select(func.count()).where(Reminder.user_id == user_id, Reminder.active))


async def fire_due(send: Callable[[int, str], Awaitable[bool]]) -> int:
    """Отправляет наступившие напоминания. FOR UPDATE SKIP LOCKED: если запущено два процесса,
    одно напоминание не уйдёт дважды."""
    now = datetime.now(ZoneInfo("UTC"))
    sent = 0
    async with Session() as s, s.begin():
        due = (
            await s.execute(
                select(Reminder, User.timezone)
                .join(User, User.id == Reminder.user_id)
                .where(Reminder.active, Reminder.fire_at <= now)
                .order_by(Reminder.fire_at)
                .limit(100)
                .with_for_update(of=Reminder, skip_locked=True)
            )
        ).all()
        for r, tz in due:
            await send(r.user_id, f"⏰ Напоминание: {r.text}")
            sent += 1
            nxt = next_time(r, ZoneInfo(tz), now.astimezone(ZoneInfo(tz)))
            if nxt is None:
                r.active = False
            else:
                r.fire_at = nxt
    return sent
