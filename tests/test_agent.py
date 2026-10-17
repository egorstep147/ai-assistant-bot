"""Агентный цикл на поддельной модели: вызовы инструментов, их результаты и итоговый ответ."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from assistant.agent import core
from assistant.agent.registry import Ctx
from assistant.db import Session
from assistant.db.models import Memory, Reminder, User


async def make_user(uid: int, **kw) -> User:
    async with Session() as s, s.begin():
        user = User(id=uid, first_name="Егор", timezone="Asia/Kamchatka", **kw)
        s.add(user)
    return user


async def run(user, text, statuses=None, confirms=None):
    async def status(t):
        (statuses if statuses is not None else []).append(t)

    async def confirm(q, action, obj_id):
        (confirms if confirms is not None else []).append((action, obj_id))

    ctx = Ctx(user=user, status=status, confirm=confirm)
    deltas, done = [], None
    async for ev in core.run(ctx, text):
        if isinstance(ev, core.Delta):
            deltas.append(ev.text)
        else:
            done = ev
    return "".join(deltas), done


async def test_plain_answer_streams(llm):
    user = await make_user(100)
    llm.script = ["Привет! Чем помочь?"]
    streamed, done = await run(user, "привет")
    assert streamed == done.text == "Привет! Чем помочь?"
    call = llm.calls[0]
    assert "Asia/Kamchatka" in call["system"] and "remember" in call["tools"]
    assert "web_search" not in call["tools"]  # без ключа Tavily поиск выключен


async def test_tool_call_then_answer(llm):
    user = await make_user(101)
    llm.script = [[("remember", {"fact": "Аллергия на орехи"})], "Запомнил!"]
    statuses = []
    _, done = await run(user, "запомни, что у меня аллергия на орехи", statuses)
    assert done.text == "Запомнил!" and done.tools == ["remember"] and statuses == ["🧠 Запоминаю…"]
    assert done.usage.input == 200  # два шага по 100 токенов
    tool_result = llm.calls[1]["messages"][-1]
    assert tool_result.role == "tool" and tool_result.text.startswith("Сохранено")
    async with Session() as s:
        assert (await s.scalar(select(Memory.text).where(Memory.user_id == 101))) == "Аллергия на орехи"
    # Во втором разговоре факт уже в системном промпте
    llm.script = ["У вас аллергия на орехи"]
    await run(user, "что ты обо мне знаешь?")
    assert "Аллергия на орехи" in llm.calls[-1]["system"]


async def test_reminder_in_user_timezone(llm):
    user = await make_user(102)
    tz = ZoneInfo("Asia/Kamchatka")
    at = (datetime.now(tz) + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    llm.script = [
        [("set_reminder", {"text": "пара", "at": at.strftime("%Y-%m-%dT%H:%M"), "repeat": "weekdays"})],
        "Напомню!",
    ]
    await run(user, "напоминай по будням в 9 про пару")
    async with Session() as s:
        r = await s.scalar(select(Reminder).where(Reminder.user_id == 102))
    assert r.fire_at == at and r.repeat == "weekdays"  # 9:00 по Камчатке, а не по серверу


async def test_past_time_and_bad_args_go_back_to_model(llm):
    user = await make_user(103)
    llm.script = [
        [
            ("set_reminder", {"text": "x", "at": "2020-01-01T10:00"}),
            ("calculate", {"expression": "__import__('os')"}),
            ("no_such_tool", {}),
        ],
        "Уточните время",
    ]
    _, done = await run(user, "напомни вчера")
    results = [m.text for m in llm.calls[1]["messages"] if m.role == "tool"]
    assert results[0].startswith("Ошибка: это время уже прошло")
    assert results[1].startswith("Ошибка в аргументах")
    assert results[2] == "Ошибка: инструмента no_such_tool нет"
    assert done.text == "Уточните время"


async def test_delete_needs_confirmation(llm):
    user = await make_user(104)
    llm.script = [[("forget", {"memory_id": 5})], "Подтвердите удаление кнопкой"]
    confirms = []
    _, done = await run(user, "забудь про аллергию", confirms=confirms)
    assert confirms == [("memory", 5)]  # агент сам не удаляет — только просит подтверждение


async def test_step_limit_stops_endless_tools(llm):
    user = await make_user(105)
    llm.script = [[("calculate", {"expression": "1+1"})]] * 10 + ["Готово"]
    _, done = await run(user, "считай бесконечно")
    assert len(llm.calls) == core.MAX_STEPS
    assert llm.calls[-1]["tools"] == []  # на последнем шаге инструменты не даём — модель обязана ответить
