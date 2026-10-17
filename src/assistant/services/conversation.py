"""История диалога: последние сообщения + краткое содержание всего, что было раньше.
Так модель «помнит» длинный разговор, а запрос не растёт бесконечно (токены = деньги)."""

import logging

from sqlalchemy import delete, func, select

from assistant.config import settings
from assistant.db import AsyncSession, Session
from assistant.db.models import Message as Row
from assistant.db.models import User
from assistant.llm import Message, Usage, get_llm

log = logging.getLogger(__name__)
KEEP = 20  # столько последних сообщений модель видит дословно
SUMMARIZE_AFTER = 40  # когда несжатых набралось столько — сжимаем старые


async def history(s: AsyncSession, user: User) -> list[Message]:
    rows = await s.scalars(
        select(Row).where(Row.user_id == user.id, Row.id > user.summary_upto).order_by(Row.id.desc()).limit(KEEP)
    )
    return [Message(role=r.role, text=r.content) for r in reversed(list(rows))]


async def save_turn(s: AsyncSession, user_id: int, question: str, answer: str) -> None:
    s.add_all(
        [
            Row(user_id=user_id, role="user", content=question[:8000]),
            Row(user_id=user_id, role="assistant", content=answer[:8000]),
        ]
    )


async def reset(s: AsyncSession, user: User) -> None:
    """/new — начать разговор заново: история и сжатое содержание удаляются, память и заметки остаются."""
    await s.execute(delete(Row).where(Row.user_id == user.id))
    user.summary, user.summary_upto = "", 0


async def maybe_summarize(user_id: int) -> Usage:
    """Сжимает старую часть диалога в несколько абзацев. Запускается в фоне после ответа."""
    async with Session() as s, s.begin():
        user = await s.get(User, user_id, with_for_update=True)
        pending = await s.scalar(select(func.count()).where(Row.user_id == user_id, Row.id > user.summary_upto))
        if pending <= SUMMARIZE_AFTER:
            return Usage()
        rows = list(
            await s.scalars(
                select(Row)
                .where(Row.user_id == user_id, Row.id > user.summary_upto)
                .order_by(Row.id)
                .limit(pending - KEEP)
            )
        )
        dialog = "\n".join(f"{'Пользователь' if r.role == 'user' else 'Ассистент'}: {r.content[:1500]}" for r in rows)
        reply = await get_llm().complete(
            model=settings.model_fast,
            tools=[],
            system="Ты сжимаешь историю переписки для памяти ассистента. Сохрани факты, договорённости, "
            "открытые вопросы и предпочтения пользователя. Не больше 250 слов, по-русски.",
            messages=[
                Message(role="user", text=f"Прежнее содержание:\n{user.summary or '—'}\n\nНовая часть:\n{dialog}")
            ],
        )
        user.summary, user.summary_upto = reply.text.strip(), rows[-1].id
        log.info("Сжата история пользователя %s: %s сообщений", user_id, len(rows))
        return reply.usage
