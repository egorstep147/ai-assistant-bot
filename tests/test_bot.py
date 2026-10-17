"""Сквозные сценарии: апдейт Telegram → роутеры aiogram → агент → ответы бота (без сети)."""

import itertools
from datetime import UTC, datetime

import pytest
from aiogram import Bot
from aiogram.methods import AnswerPreCheckoutQuery, CreateInvoiceLink, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, PreCheckoutQuery, SuccessfulPayment, Update
from aiogram.types import User as TgUser
from sqlalchemy import func, select

from assistant.bot import create_dispatcher
from assistant.db import Session
from assistant.db.models import Memory, Payment, User
from tests.fakes import FakeSession

ids = itertools.count(1)


@pytest.fixture(scope="session")
def tg():
    # Один диспетчер на все тесты: роутеры aiogram подключаются к диспетчеру только один раз
    session = FakeSession()
    return Bot("123456:TEST-TOKEN", session=session), session, create_dispatcher()


def user(uid: int) -> TgUser:
    return TgUser(id=uid, is_bot=False, first_name="Егор")


async def send(tg, uid: int, text: str = "", **kw):
    bot, session, dp = tg
    session.requests.clear()
    msg = Message(
        message_id=next(ids),
        date=datetime.now(UTC),
        chat=Chat(id=uid, type="private"),
        from_user=user(uid),
        text=text or None,
        **kw,
    )
    await dp.feed_update(bot, Update(update_id=next(ids), message=msg))
    return session


async def press(tg, uid: int, data: str):
    bot, session, dp = tg
    session.requests.clear()
    msg = Message(message_id=next(ids), date=datetime.now(UTC), chat=Chat(id=uid, type="private"), text="…")
    cb = CallbackQuery(id=str(next(ids)), from_user=user(uid), chat_instance="x", message=msg, data=data)
    await dp.feed_update(bot, Update(update_id=next(ids), callback_query=cb))
    return session


async def test_chat_reply_is_streamed_and_formatted(tg, llm):
    llm.script = ["Вот **главное**: всё работает"]
    session = await send(tg, 500, "привет")
    texts = session.texts()
    assert texts[-1] == "Вот <b>главное</b>: всё работает"  # финал — HTML-разметка Telegram
    assert any(isinstance(m, SendMessage) for m in session.requests)


async def test_daily_limit_on_free_plan(tg, llm):
    llm.script = ["1", "2", "3"]
    for _ in range(3):
        await send(tg, 501, "вопрос")
    session = await send(tg, 501, "ещё вопрос")
    assert "Лимит на сегодня исчерпан (3 сообщений)" in session.texts()[-1]
    assert len(llm.calls) == 3  # четвёртый запрос до модели не дошёл — деньги не потрачены


async def test_new_resets_history(tg, llm):
    llm.script = ["ответ"]
    await send(tg, 502, "первое")
    await send(tg, 502, "/new")
    llm.script = ["снова"]
    await send(tg, 502, "второе")
    history = [m.text for m in llm.calls[-1]["messages"]]
    assert history == ["второе"]


async def test_forget_with_confirmation(tg, llm):
    async with Session() as s, s.begin():
        s.add(User(id=503, first_name="Е"))
        await s.flush()
        s.add(Memory(user_id=503, text="Любит чай"))
    async with Session() as s:
        mid = await s.scalar(select(Memory.id).where(Memory.user_id == 503))
    llm.script = [[("forget", {"memory_id": mid})], "Подтвердите кнопкой"]
    session = await send(tg, 503, "забудь про чай")
    confirm = next(m for m in session.requests if isinstance(m, SendMessage) and m.text.startswith("❓"))
    yes = confirm.reply_markup.inline_keyboard[0][0].callback_data
    await press(tg, 999, yes)  # чужой пользователь кнопкой не воспользуется
    async with Session() as s:
        assert await s.scalar(select(func.count()).where(Memory.user_id == 503)) == 1
    await press(tg, 503, yes)
    async with Session() as s:
        assert await s.scalar(select(func.count()).where(Memory.user_id == 503)) == 0


async def test_voice_is_transcribed(tg, llm, monkeypatch):
    from aiogram.types import Voice

    async def fake_download(self, file, *a, **kw):
        import io

        return io.BytesIO(b"OggS")

    monkeypatch.setattr(Bot, "download", fake_download)
    llm.script = ["Напомню купить хлеб"]
    session = await send(tg, 504, voice=Voice(file_id="f", file_unique_id="u", duration=2, file_size=100))
    assert "напомни купить хлеб" in session.texts()[0]  # сначала показываем расшифровку
    assert llm.calls[-1]["messages"][-1].text == "напомни купить хлеб"


async def test_plan_and_payments(tg, llm):
    bot, session, dp = tg
    session = await send(tg, 505, "/plan")
    link = next(m for m in session.requests if isinstance(m, CreateInvoiceLink))
    assert link.currency == "XTR" and link.subscription_period == 30 * 24 * 3600

    async def checkout(payload, currency, amount):
        session.requests.clear()
        q = PreCheckoutQuery(
            id="q", from_user=user(505), currency=currency, total_amount=amount, invoice_payload=payload
        )
        await dp.feed_update(bot, Update(update_id=next(ids), pre_checkout_query=q))
        return next(m for m in session.requests if isinstance(m, AnswerPreCheckoutQuery)).ok

    assert await checkout("pro:stars", "XTR", 250) is True
    assert await checkout("pro:stars", "XTR", 1) is False  # подменённая сумма не пройдёт
    assert await checkout("pro:card", "RUB", 39900) is True

    until = int(datetime(2030, 1, 1, tzinfo=UTC).timestamp())
    paid = SuccessfulPayment(
        currency="XTR",
        total_amount=250,
        invoice_payload="pro:stars",
        telegram_payment_charge_id="ch-1",
        provider_payment_charge_id="",
        subscription_expiration_date=until,
        is_recurring=True,
        is_first_recurring=True,
    )
    for _ in range(2):  # Telegram может прислать апдейт повторно — платёж учитывается один раз
        await send(tg, 505, successful_payment=paid)
    async with Session() as s:
        u = await s.get(User, 505)
        assert u.pro_until == datetime(2030, 1, 1, tzinfo=UTC)
        assert await s.scalar(select(func.count()).where(Payment.user_id == 505)) == 1
    llm.script = ["pro-ответ"]
    await send(tg, 505, "вопрос")
    from assistant.config import settings

    assert llm.calls[-1]["model"] == settings.model_smart  # Pro отвечает умной моделью


async def test_admin_only(tg, llm):
    session = await send(tg, 506, "/admin")
    llm.script = []
    assert not any("Пользователей" in t for t in session.texts())  # не админ — команда уходит в обычный чат
    session = await send(tg, 1, "/admin")
    assert "Пользователей:" in session.texts()[-1]
