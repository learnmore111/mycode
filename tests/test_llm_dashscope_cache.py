from types import SimpleNamespace

from mycode.provider.schema import Model, ModelApi, ModelCapabilities, ModelLimit
from mycode.session.llm import (
    StreamInput,
    _build_messages,
    _get_cache_read_tokens,
    _get_cache_write_tokens,
    _serialize_usage,
)


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


def test_build_messages_uses_explicit_cache_for_other_supported_dashscope_models() -> None:
    stream_input = StreamInput(
        model=_make_model("qwen3-coder-plus"),
        system=["system prompt body"],
        messages=[{"role": "user", "content": "hello"}],
    )

    messages = _build_messages(stream_input)

    assert messages[0]["content"][0]["cache_control"] == {"type": "ephemeral"}


def test_build_messages_uses_explicit_cache_for_supported_dashscope_snapshot_model() -> None:
    stream_input = StreamInput(
        model=_make_model("qwen3.5-plus-2026-04-20"),
        system=["system prompt body"],
        messages=[{"role": "user", "content": "hello"}],
    )

    messages = _build_messages(stream_input)

    assert messages[0]["content"][0]["cache_control"] == {"type": "ephemeral"}


def test_build_messages_keeps_plain_system_for_other_models() -> None:
    stream_input = StreamInput(
        model=_make_model("gpt-4o", pid="openai", npm="@ai-sdk/openai"),
        system=["system prompt body"],
        messages=[{"role": "user", "content": "hello"}],
    )

    messages = _build_messages(stream_input)

    assert messages[0] == {"role": "system", "content": "system prompt body"}


def test_build_messages_keeps_plain_system_for_unsupported_dashscope_model() -> None:
    stream_input = StreamInput(
        model=_make_model("qwen-plus-us"),
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


def test_get_cache_read_tokens_from_input_tokens_details() -> None:
    usage = SimpleNamespace(
        input_tokens_details=SimpleNamespace(cached_tokens=512),
    )

    assert _get_cache_read_tokens(usage) == 512


def test_get_cache_read_tokens_from_top_level_cached_tokens() -> None:
    usage = SimpleNamespace(cached_tokens=256)

    assert _get_cache_read_tokens(usage) == 256


def test_get_cache_write_tokens_from_nested_cache_creation_object() -> None:
    usage = SimpleNamespace(
        prompt_tokens_details=SimpleNamespace(
            cache_creation=SimpleNamespace(ephemeral_5m_input_tokens=1024),
        ),
    )

    assert _get_cache_write_tokens(usage) == 1024


def test_get_cache_tokens_from_dict_usage_payload() -> None:
    usage = {
        "input_tokens_details": {"cached_tokens": 321},
        "prompt_tokens_details": {
            "cache_creation": {"cache_creation_input_tokens": 654},
        },
    }

    assert _get_cache_read_tokens(usage) == 321
    assert _get_cache_write_tokens(usage) == 654


def test_serialize_usage_keeps_nested_payload() -> None:
    usage = SimpleNamespace(
        prompt_tokens=12,
        prompt_tokens_details=SimpleNamespace(cached_tokens=9),
        completion_tokens_details={"reasoning_tokens": 4},
    )

    assert _serialize_usage(usage) == {
        "prompt_tokens": 12,
        "prompt_tokens_details": {"cached_tokens": 9},
        "completion_tokens_details": {"reasoning_tokens": 4},
    }
