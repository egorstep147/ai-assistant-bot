"""Перевод общего формата сообщений в формат каждого провайдера — проверка без сети и ключей."""

from types import SimpleNamespace

from assistant.llm import Attachment, Message, ToolCall, gemini
from assistant.llm import anthropic as anth
from assistant.llm import openai as oai

HISTORY = [
    Message(role="user", text="Погода и курс?", attachments=[Attachment("image/png", b"\x89PNG")]),
    Message(
        role="assistant",
        text="Сейчас узнаю",
        tool_calls=[ToolCall("a", "weather", {"city": "Москва"}), ToolCall("b", "currency", {"amount": 1})],
    ),
    Message(role="tool", text="+5°C", tool_call_id="a", name="weather"),
    Message(role="tool", text="90 ₽", tool_call_id="b", name="currency"),
]


def test_gemini_groups_function_responses():
    contents = gemini._contents(HISTORY)
    assert [c.role for c in contents] == ["user", "model", "user"]
    assert contents[0].parts[0].inline_data.mime_type == "image/png"
    assert [p.function_call.name for p in contents[1].parts if p.function_call] == ["weather", "currency"]
    responses = [p.function_response for p in contents[2].parts]  # оба ответа — одним сообщением
    assert [(r.name, r.id, r.response) for r in responses] == [
        ("weather", "a", {"result": "+5°C"}),
        ("currency", "b", {"result": "90 ₽"}),
    ]


def test_gemini_keeps_raw_model_turn():
    raw = gemini.types.Content(role="model", parts=[gemini.types.Part(text="x", thought_signature=b"sig")])
    contents = gemini._contents([Message(role="assistant", raw=raw)])
    assert contents[0] is raw  # подпись мысли уходит обратно как есть


def test_anthropic_tool_results_in_one_user_message():
    msgs = anth._messages(HISTORY)
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert msgs[0]["content"][0]["type"] == "image"
    assert [b["type"] for b in msgs[1]["content"]] == ["text", "tool_use", "tool_use"]
    assert [b["tool_use_id"] for b in msgs[2]["content"]] == ["a", "b"]


def test_openai_format():
    msgs = oai._messages("sys", HISTORY)
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "tool", "tool"]
    assert msgs[1]["content"][0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert msgs[2]["tool_calls"][0]["function"] == {"name": "weather", "arguments": '{"city": "Москва"}'}


async def test_openai_assembles_streamed_tool_calls():
    def chunk(content=None, calls=None, usage=None):
        delta = SimpleNamespace(content=content, tool_calls=calls)
        return SimpleNamespace(choices=[SimpleNamespace(delta=delta)] if usage is None else [], usage=usage)

    def call(index, id=None, name=None, args=None):
        return SimpleNamespace(index=index, id=id, function=SimpleNamespace(name=name, arguments=args))

    chunks = [
        chunk("Счит"),
        chunk("аю"),
        chunk(calls=[call(0, "x1", "calcu", '{"expr')]),
        chunk(calls=[call(0, None, "late", 'ession": "2+2"}')]),
        chunk(usage=SimpleNamespace(prompt_tokens=50, completion_tokens=7)),
    ]

    async def stream():
        for c in chunks:
            yield c

    llm = oai.OpenAILLM("key")

    async def create(**kw):
        return stream()

    llm.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    items = [i async for i in llm._stream(model="m", system="s", messages=[], tools=[])]
    reply = items[-1]
    assert items[:2] == ["Счит", "аю"] and reply.text == "Считаю"
    assert reply.tool_calls == [ToolCall("x1", "calculate", {"expression": "2+2"})]
    assert (reply.usage.input, reply.usage.output) == (50, 7)
