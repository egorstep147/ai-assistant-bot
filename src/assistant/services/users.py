from aiogram.types import User as TgUser
from sqlalchemy import delete, func, update
from sqlalchemy.dialects.postgresql import insert

from assistant.config import settings
from assistant.db import AsyncSession
from assistant.db.models import Chunk, Document, Memory, Message, Note, Reminder, Task, User


async def touch(s: AsyncSession, tg: TgUser) -> User:
    """Найти или создать пользователя и обновить имя и «последний визит» — одним запросом."""
    stmt = insert(User).values(
        id=tg.id, first_name=tg.first_name or "", username=tg.username, timezone=settings.default_timezone
    )
    await s.execute(
        stmt.on_conflict_do_update(
            index_elements=[User.id],
            set_={
                "first_name": stmt.excluded.first_name,
                "username": stmt.excluded.username,
                "last_seen": func.now(),
                "blocked": False,
            },
        )
    )
    return await s.get(User, tg.id, populate_existing=True)


async def delete_all_data(s: AsyncSession, user_id: int) -> None:
    """Право на удаление данных: всё, кроме платежей (их храним для бухгалтерии и возвратов)."""
    for model in (Message, Memory, Note, Task, Reminder, Chunk, Document):
        await s.execute(delete(model).where(model.user_id == user_id))
    await s.execute(update(User).where(User.id == user_id).values(summary="", summary_upto=0))
