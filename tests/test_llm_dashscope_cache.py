from types import SimpleNamespace

from mycode.provider.schema import Model, ModelApi, ModelCapabilities, ModelLimit
from mycode.session.llm import _build_messages, _get_cache_read_tokens, _get_cache_write_tokens, StreamInput


def _make_model(mid: str, pid: str = "dashscope", npm: str = "@ai-sdk/openai-compatible") -> Model:
    return Model(
        id=mid,
        providerID=pid,
        api=ModelApi(id=mid, npm=npm),
        name=mid,
        capabilities=ModelCapabilities(),
        limit=ModelLimit(),
    )


def test_build_messages_uses_explicit_cache_for_dashscope_qwen36_plus() -> None:
    stream_input = StreamInput(
        model=_make_model("qwen3.6-plus"),
        system=["system prompt body"],
        messages=[{"role": "user", "content": "hello"}],
    )

    messages = _build_messages(stream_input)

    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == [
        {
            "type": "text",
            "text": "system prompt body",
            "cache_control": {"type": "ephemeral"},
        }
    ]
    assert messages[1] == {"role": "user", "content": "hello"}


def test_build_messages_keeps_plain_system_for_other_models() -> None:
    stream_input = StreamInput(
        model=_make_model("gpt-4o", pid="openai", npm="@ai-sdk/openai"),
        system=["system prompt body"],
        messages=[{"role": "user", "content": "hello"}],
    )

    messages = _build_messages(stream_input)

    assert messages[0] == {"role": "system", "content": "system prompt body"}


def test_get_cache_read_tokens_from_prompt_tokens_details() -> None:
    usage = SimpleNamespace(
        prompt_tokens_details=SimpleNamespace(cached_tokens=2048),
    )

    assert _get_cache_read_tokens(usage) == 2048


def test_get_cache_write_tokens_from_prompt_tokens_details() -> None:
    usage = SimpleNamespace(
        prompt_tokens_details=SimpleNamespace(cache_creation_input_tokens=1605),
    )

    assert _get_cache_write_tokens(usage) == 1605
