"""Реестр инструментов агента. Инструмент = обычная async-функция + описание и JSON Schema аргументов.
Модель видит описания, решает, что вызвать, а мы исполняем вызов и отдаём ей результат текстом."""

import inspect
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from assistant.db.models import User
from assistant.llm import ToolCall, ToolSpec

log = logging.getLogger(__name__)


@dataclass
class Ctx:
    """Что инструмент знает о текущем разговоре."""

    user: User
    status: Callable[[str], Awaitable[None]]  # показать «🔎 Ищу…» под ответом
    confirm: Callable[[str, str, int], Awaitable[None]]  # (вопрос, действие, id) — кнопки «Да/Нет»
    used: list[str] = field(default_factory=list)  # какие инструменты вызывались (для логов и тестов)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.user.timezone)

    def now(self) -> datetime:
        return datetime.now(self.tz)


@dataclass
class Tool:
    spec: ToolSpec
    fn: Callable[..., Awaitable[str]]
    status: str
    enabled: Callable[[User], bool]


TOOLS: dict[str, Tool] = {}


def tool(
    name: str, description: str, *, status: str = "", enabled: Callable[[User], bool] = lambda u: True, **params: dict
):
    """Регистрирует инструмент. params — свойства JSON Schema; обязательные — без "optional": True."""

    def deco(fn):
        props = {k: {kk: vv for kk, vv in v.items() if kk != "optional"} for k, v in params.items()}
        required = [k for k, v in params.items() if not v.get("optional")]
        schema = {"type": "object", "properties": props, "required": required}
        TOOLS[name] = Tool(ToolSpec(name, description, schema), fn, status, enabled)
        return fn

    return deco


def specs(user: User) -> list[ToolSpec]:
    return [t.spec for t in TOOLS.values() if t.enabled(user)]


async def call(ctx: Ctx, tc: ToolCall) -> str:
    t = TOOLS.get(tc.name)
    if t is None or not t.enabled(ctx.user):
        return f"Ошибка: инструмента {tc.name} нет"
    allowed = inspect.signature(t.fn).parameters
    args = {k: v for k, v in tc.args.items() if k in allowed}  # лишние аргументы модели отбрасываем
    ctx.used.append(tc.name)
    if t.status:
        await ctx.status(t.status)
    try:
        result = await t.fn(ctx, **args)
    except (TypeError, ValueError) as e:
        return f"Ошибка в аргументах: {e}"
    except Exception as e:
        log.exception("Инструмент %s упал", tc.name)
        return f"Ошибка инструмента: {type(e).__name__}"
    return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
