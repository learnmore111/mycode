"""Tests for provider schema and transform."""
import pytest
from mycode.provider.schema import (
    Model, ModelApi, ModelCapabilities, ModelCost, ModelLimit, CacheCost, ProviderInfo,
)
from mycode.provider.transform import (
    temperature, top_p, max_tokens, supports_cache, reasoning_params, build_litellm_kwargs,
)
from mycode.provider.provider import parse_model, litellm_model_name


def _make_model(mid="claude-sonnet-4", pid="anthropic", npm="@ai-sdk/anthropic", **kw) -> Model:
    return Model(
        id=mid, providerID=pid,
        api=ModelApi(id=mid, npm=npm),
        name=mid,
        capabilities=kw.get("capabilities", ModelCapabilities()),
        limit=kw.get("limit", ModelLimit()),
        **{k: v for k, v in kw.items() if k not in ("capabilities", "limit")},
    )


def test_parse_model():
    pid, mid = parse_model("anthropic/claude-3")
    assert pid == "anthropic"
    assert mid == "claude-3"


def test_parse_model_invalid():
    with pytest.raises(ValueError):
        parse_model("no-slash")


def test_litellm_model_name():
    m = _make_model("claude-sonnet-4-20250514", "anthropic")
    assert litellm_model_name(m) == "anthropic/claude-sonnet-4-20250514"


def test_litellm_model_name_google():
    m = _make_model("gemini-2.0", "google")
    assert litellm_model_name(m) == "gemini/gemini-2.0"


def test_temperature_claude():
    m = _make_model("claude-sonnet-4")
    assert temperature(m) is None


def test_temperature_qwen():
    m = _make_model("qwen-2.5-coder")
    assert temperature(m) == 0.55


def test_temperature_gemini():
    m = _make_model("gemini-2.0-flash")
    assert temperature(m) == 1.0


def test_top_p_qwen():
    m = _make_model("qwen-72b")
    assert top_p(m) == 1.0


def test_top_p_default():
    m = _make_model("claude-sonnet-4")
    assert top_p(m) is None


def test_max_tokens_with_limit():
    m = _make_model(limit=ModelLimit(output=16000))
    assert max_tokens(m) == 16000


def test_max_tokens_capped():
    m = _make_model(limit=ModelLimit(output=999999))
    assert max_tokens(m) == 32000


def test_max_tokens_zero():
    m = _make_model(limit=ModelLimit(output=0))
    assert max_tokens(m) is None


def test_supports_cache_anthropic():
    m = _make_model(npm="@ai-sdk/anthropic")
    assert supports_cache(m) is True


def test_supports_cache_unknown():
    m = _make_model(npm="some-unknown-npm")
    assert supports_cache(m) is False


def test_reasoning_params_no_reasoning():
    m = _make_model(capabilities=ModelCapabilities(reasoning=False))
    assert reasoning_params(m) == {}


def test_reasoning_params_anthropic():
    m = _make_model(npm="@ai-sdk/anthropic", capabilities=ModelCapabilities(reasoning=True))
    params = reasoning_params(m)
    assert "thinking" in params
    assert params["thinking"]["budget_tokens"] == 10000


def test_reasoning_params_anthropic_hard():
    m = _make_model(npm="@ai-sdk/anthropic", capabilities=ModelCapabilities(reasoning=True))
    params = reasoning_params(m, "think_hard")
    assert params["thinking"]["budget_tokens"] == 30000


def test_build_litellm_kwargs():
    m = _make_model("qwen-72b", limit=ModelLimit(output=8000),
                     capabilities=ModelCapabilities(reasoning=False))
    kwargs = build_litellm_kwargs(m)
    assert kwargs["max_tokens"] == 8000
    assert kwargs["temperature"] == 0.55
    assert kwargs["top_p"] == 1.0


def test_provider_info():
    p = ProviderInfo(id="test", name="Test", source="env")
    assert p.id == "test"
    assert p.models == {}
    assert p.key is None


def test_model_cost():
    c = ModelCost(input=3.0, output=15.0, cache=CacheCost(read=0.3, write=3.75))
    assert c.input == 3.0
    assert c.cache.read == 0.3
