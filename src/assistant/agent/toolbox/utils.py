"""Бесплатные API без ключей: погода (Open-Meteo), курсы ЦБ РФ; безопасный калькулятор."""

import ast
import math
import operator

import httpx

from assistant.agent.registry import Ctx, tool

TIMEOUT = httpx.Timeout(10, connect=5)
WEATHER = {
    0: "ясно",
    1: "в основном ясно",
    2: "переменная облачность",
    3: "пасмурно",
    45: "туман",
    48: "изморозь",
    51: "морось",
    53: "морось",
    55: "сильная морось",
    61: "небольшой дождь",
    63: "дождь",
    65: "ливень",
    66: "ледяной дождь",
    67: "ледяной дождь",
    71: "небольшой снег",
    73: "снег",
    75: "сильный снег",
    77: "снежная крупа",
    80: "ливни",
    81: "ливни",
    82: "сильные ливни",
    85: "снегопад",
    86: "сильный снегопад",
    95: "гроза",
    96: "гроза с градом",
    99: "гроза с градом",
}


@tool("weather", "Погода сейчас и прогноз на 3 дня для города.", status="🌤 Смотрю погоду…", city={"type": "string"})
async def weather(ctx: Ctx, city: str) -> dict | str:
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        geo = (
            (
                await c.get(
                    "https://geocoding-api.open-meteo.com/v1/search",
                    params={"name": city, "count": 1, "language": "ru"},
                )
            )
            .json()
            .get("results")
        )
        if not geo:
            return f"Город «{city}» не найден"
        g = geo[0]
        f = (
            await c.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": g["latitude"],
                    "longitude": g["longitude"],
                    "timezone": "auto",
                    "forecast_days": 3,
                    "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
                    "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
                    "wind_speed_unit": "ms",
                },
            )
        ).json()
    cur, d = f["current"], f["daily"]
    return {
        "place": f"{g['name']}, {g.get('country', '')}",
        "now": f"{cur['temperature_2m']:+.0f}°C (ощущается {cur['apparent_temperature']:+.0f}°C), "
        f"{WEATHER.get(cur['weather_code'], '')}, ветер {cur['wind_speed_10m']:.0f} м/с",
        "days": [
            f"{d['time'][i]}: {d['temperature_2m_min'][i]:+.0f}…{d['temperature_2m_max'][i]:+.0f}°C, "
            f"{WEATHER.get(d['weather_code'][i], '')}, осадки {d['precipitation_probability_max'][i]}%"
            for i in range(len(d["time"]))
        ],
    }


@tool(
    "currency",
    "Перевести сумму между валютами по курсу ЦБ РФ на сегодня. Коды: RUB, USD, EUR, CNY…",
    status="💱 Смотрю курс…",
    amount={"type": "number"},
    from_code={"type": "string"},
    to_code={"type": "string"},
)
async def currency(ctx: Ctx, amount: float, from_code: str, to_code: str) -> str:
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        data = (await c.get("https://www.cbr-xml-daily.ru/daily_json.js")).json()
    rates = {k: v["Value"] / v["Nominal"] for k, v in data["Valute"].items()} | {"RUB": 1.0}
    a, b = from_code.upper(), to_code.upper()
    if a not in rates or b not in rates:
        return f"Неизвестная валюта: {a if a not in rates else b}"
    result = amount * rates[a] / rates[b]
    return f"{amount:g} {a} = {result:,.2f} {b} (курс ЦБ на {data['Date'][:10]})".replace(",", " ")


# Калькулятор через разбор выражения в дерево: eval() выполнил бы любой код, а здесь — только арифметика
_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}
_FUNCS = {k: getattr(math, k) for k in ("sqrt", "sin", "cos", "tan", "log", "log10", "exp", "floor", "ceil")} | {
    "abs": abs,
    "round": round,
    "pi": math.pi,
    "e": math.e,
}


def safe_eval(expr: str) -> float:
    def ev(n):
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            left, right = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Pow) and abs(right) > 1000:
                raise ValueError("слишком большая степень")
            return _OPS[type(n.op)](left, right)
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        if isinstance(n, ast.Name) and n.id in _FUNCS and not callable(_FUNCS[n.id]):
            return _FUNCS[n.id]
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and callable(_FUNCS.get(n.func.id)):
            return _FUNCS[n.func.id](*[ev(a) for a in n.args])
        raise ValueError("допустимы только числа, + - * / ** % и функции sqrt, sin, log…")

    return ev(ast.parse(expr.replace("^", "**").replace(",", "."), mode="eval").body)


@tool(
    "calculate",
    "Точно посчитать арифметическое выражение: 2*(3+4)**2, sqrt(16), 15% от 2400 = 2400*0.15.",
    expression={"type": "string"},
)
async def calculate(ctx: Ctx, expression: str) -> str:
    return f"{expression} = {safe_eval(expression):.10g}"
