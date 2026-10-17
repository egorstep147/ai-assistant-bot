from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from assistant.agent.toolbox.utils import safe_eval
from assistant.agent.toolbox.web import _is_public, html_to_text
from assistant.db import Session
from assistant.db.models import Reminder, User
from assistant.services import memory, reminders


@pytest.mark.parametrize(
    "expr, value",
    [
        ("2*(3+4)**2", 98),
        ("sqrt(16)+abs(-2)", 6),
        ("2400*0.15", 360),
        ("10 % 3", 1),
        ("2^10", 1024),
        ("1,5*2", 3.0),
        ("-pi", -3.141592653589793),
    ],
)
def test_calculator(expr, value):
    assert safe_eval(expr) == pytest.approx(value)


@pytest.mark.parametrize("expr", ["__import__('os').system('ls')", "open('x')", "9**9**9", "[1,2]", "a+1"])
def test_calculator_rejects_code(expr):
    with pytest.raises((ValueError, SyntaxError)):
        safe_eval(expr)


def test_html_to_text():
    page = (
        "<html><head><title>x</title></head>"
        "<body><script>evil()</script><h1>Заголовок</h1><p>Текст &amp; ещё</p></body>"
    )
    assert html_to_text(page) == "Заголовок\n Текст & ещё"


async def test_ssrf_guard():
    assert not await _is_public("localhost")
    assert not await _is_public("127.0.0.1")
    assert not await _is_public("10.0.0.5")
    assert not await _is_public("169.254.169.254")  # метаданные облака — классическая цель SSRF


def test_split_text_overlaps():
    text = ". ".join(f"Предложение номер {i}" for i in range(400))
    parts = memory.split_text(text, size=500, overlap=100)
    assert all(len(p) <= 500 for p in parts) and len(parts) > 10
    assert parts[0][-50:] in parts[1]  # кусок с перекрытием: граница не теряется


def test_next_time_keeps_local_hour_across_dst():
    tz = ZoneInfo("Europe/Berlin")
    r = Reminder(fire_at=datetime(2026, 10, 24, 9, 0, tzinfo=tz), repeat="daily")
    nxt = reminders.next_time(r, tz, datetime(2026, 10, 25, 12, 0, tzinfo=tz))
    assert (nxt.day, nxt.hour) == (26, 9)  # 25-го перевели часы, но напоминание всё равно в 9:00


@pytest.mark.parametrize(
    "repeat, start, expected",
    [
        ("weekdays", datetime(2026, 10, 23, 8), datetime(2026, 10, 26, 8)),  # пятница → понедельник
        ("monthly", datetime(2026, 1, 31, 8), datetime(2026, 2, 28, 8)),  # 31-го нет в феврале
        ("none", datetime(2026, 1, 1, 8), None),
    ],
)
def test_next_time_repeats(repeat, start, expected):
    tz = ZoneInfo("UTC")
    r = Reminder(fire_at=start.replace(tzinfo=tz), repeat=repeat)
    nxt = reminders.next_time(r, tz, start.replace(tzinfo=tz))
    assert nxt == (expected.replace(tzinfo=tz) if expected else None)


async def test_memory_search_by_meaning():
    async with Session() as s, s.begin():
        s.add(User(id=10, first_name="A"))
        await s.flush()
        for fact in ["Аллергия на орехи", "Живёт в Петропавловске", "Любит кофе без сахара", "Учится в КамГУ"] * 5:
            await memory.remember(s, 10, fact)
    async with Session() as s:
        found = await memory.memories(s, 10, "какая у меня аллергия?", limit=3)
    assert found[0].text == "Аллергия на орехи"


async def test_reminders_fire_once_and_repeat():
    now = datetime.now(ZoneInfo("UTC"))
    async with Session() as s, s.begin():
        s.add(User(id=11, first_name="B", timezone="Asia/Kamchatka"))
        await s.flush()
        s.add_all(
            [
                Reminder(user_id=11, text="разовое", fire_at=now.replace(year=now.year - 1)),
                Reminder(user_id=11, text="каждый день", fire_at=now.replace(year=now.year - 1), repeat="daily"),
            ]
        )
    sent = []

    async def send(uid, text):
        sent.append(text)
        return True

    assert await reminders.fire_due(send) == 2
    assert await reminders.fire_due(send) == 0  # второй раз ничего не уходит: разовое выключено, повтор — завтра
    assert sorted(sent) == ["⏰ Напоминание: каждый день", "⏰ Напоминание: разовое"]
