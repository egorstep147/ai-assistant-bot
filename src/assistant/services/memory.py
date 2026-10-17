"""Поиск по смыслу (векторы) для памяти, заметок и документов. Если эмбеддинги недоступны
(нет ключа, сбой провайдера) — откатываемся на обычный поиск по словам, бот не ломается."""

import logging

from sqlalchemy import delete, func, select

from assistant.db import AsyncSession
from assistant.db.models import Chunk, Document, Memory, Note
from assistant.llm import get_embedder

log = logging.getLogger(__name__)


async def embed(texts: list[str], *, query: bool = False) -> list[list[float]] | None:
    try:
        return await get_embedder().embed(texts, query=query)
    except Exception as e:
        log.warning("Эмбеддинги недоступны: %s", e)
        return None


def _like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


async def remember(s: AsyncSession, user_id: int, text: str) -> Memory:
    vec = await embed([text])
    m = Memory(user_id=user_id, text=text.strip()[:1000], embedding=vec[0] if vec else None)
    s.add(m)
    await s.flush()
    return m


async def memories(s: AsyncSession, user_id: int, query: str = "", limit: int = 30) -> list[Memory]:
    q = select(Memory).where(Memory.user_id == user_id)
    total = await s.scalar(select(func.count()).where(Memory.user_id == user_id))
    if total <= limit:
        query = ""  # фактов мало — отдаём все, без лишнего запроса за эмбеддингом
    if query and (vec := await embed([query], query=True)):
        # cosine_distance: 0 — тот же смысл, 2 — противоположный. Сортируем по близости к запросу
        q = q.where(Memory.embedding.is_not(None)).order_by(Memory.embedding.cosine_distance(vec[0]))
    elif query:
        q = q.where(Memory.text.ilike(_like(query))).order_by(Memory.id.desc())
    else:
        q = q.order_by(Memory.id.desc())
    return list(await s.scalars(q.limit(limit)))


async def add_note(s: AsyncSession, user_id: int, title: str, text: str) -> Note:
    vec = await embed([f"{title}\n{text}"])
    n = Note(user_id=user_id, title=title.strip()[:200], text=text.strip()[:20000], embedding=vec[0] if vec else None)
    s.add(n)
    await s.flush()
    return n


async def find_notes(s: AsyncSession, user_id: int, query: str, limit: int = 5) -> list[Note]:
    q = select(Note).where(Note.user_id == user_id)
    if query and (vec := await embed([query], query=True)):
        q = q.where(Note.embedding.is_not(None)).order_by(Note.embedding.cosine_distance(vec[0]))
    elif query:
        q = q.where(Note.title.ilike(_like(query)) | Note.text.ilike(_like(query)))
    return list(await s.scalars(q.order_by(Note.id.desc()).limit(limit)))


def split_text(text: str, size: int = 1500, overlap: int = 200) -> list[str]:
    """Режем документ на куски с перекрытием: мысль на границе кусков не потеряется."""
    text = text.strip()
    chunks, start = [], 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):  # стараемся резать по абзацу или предложению
            cut = max(text.rfind("\n\n", start, end), text.rfind(". ", start, end))
            end = cut + 1 if cut > start + size // 2 else end
        chunks.append(text[start:end].strip())
        start = end - overlap if end < len(text) else end
    return [c for c in chunks if c]


async def save_document(s: AsyncSession, user_id: int, filename: str, text: str) -> Document | None:
    parts = split_text(text)
    vectors: list[list[float]] = []
    for i in range(0, len(parts), 50):  # эмбеддинги пачками — у провайдеров лимит на размер запроса
        batch = await embed(parts[i : i + 50])
        if batch is None:
            return None
        vectors += batch
    doc = Document(user_id=user_id, filename=filename[:200], chars=len(text))
    s.add(doc)
    await s.flush()
    s.add_all(
        Chunk(document_id=doc.id, user_id=user_id, idx=i, text=t, embedding=v)
        for i, (t, v) in enumerate(zip(parts, vectors, strict=True))
    )
    return doc


async def search_documents(s: AsyncSession, user_id: int, query: str, limit: int = 6) -> list[tuple[Chunk, str]]:
    vec = await embed([query], query=True)
    if vec is None:
        return []
    q = (
        select(Chunk, Document.filename)
        .join(Document, Document.id == Chunk.document_id)
        .where(Chunk.user_id == user_id)
        .order_by(Chunk.embedding.cosine_distance(vec[0]))
        .limit(limit)
    )
    return [(c, f) for c, f in (await s.execute(q)).all()]


async def delete_memory(s: AsyncSession, user_id: int, memory_id: int) -> bool:
    r = await s.execute(delete(Memory).where(Memory.id == memory_id, Memory.user_id == user_id))
    return r.rowcount > 0
