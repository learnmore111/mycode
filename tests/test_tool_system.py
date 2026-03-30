"""Comprehensive tests for the enhanced tool system.

Covers:
- CallableTool generic parameter validation
- ToolResult structured return values (ToolOk / ToolError)
- Unified error hierarchy
- ToolResultBuilder output truncation
- Description template loading
- Registry enhancements (hide/show, clear, get_or_raise)
"""
from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from opencode.tool import registry
from opencode.tool.base import (
    CallableTool,
    ToolContext,
    ToolError,
    ToolInfo,
    ToolNotFoundError,
    ToolOk,
    ToolParseError,
    ToolResult,
    ToolResultBuilder,
    ToolRuntimeError,
    ToolValidateError,
    load_description,
)

# ── Helpers ────────────────────────────────────────────────────────────


def _ctx() -> ToolContext:
    return ToolContext(session_id="test", message_id="m1", agent="build")


# ── CallableTool with Pydantic parameters ─────────────────────────────


class GreetParams(BaseModel):
    name: str = Field(description="Name to greet")
    greeting: str = Field(default="Hello", description="Greeting word")


class GreetTool(CallableTool[GreetParams]):
    id = "greet"
    description = "A test greeting tool"

    async def call(self, params: GreetParams, ctx: ToolContext) -> ToolResult:
        return ToolOk(f"{params.greeting}, {params.name}!", title="Greet")


class NumberParams(BaseModel):
    value: int = Field(description="A numeric value")
    multiplier: float = Field(default=1.0, description="Multiplier")


class NumberTool(CallableTool[NumberParams]):
    id = "number"
    description = "A numeric test tool"

    async def call(self, params: NumberParams, ctx: ToolContext) -> ToolResult:
        result = params.value * params.multiplier
        return ToolOk(str(result), title="Number")


@pytest.mark.asyncio
async def test_callable_tool_basic():
    """Basic CallableTool execution with valid params."""
    tool = GreetTool()
    result = await tool.execute({"name": "World"}, _ctx())
    assert result.output == "Hello, World!"
    assert not result.is_error


@pytest.mark.asyncio
async def test_callable_tool_with_optional():
    """CallableTool with optional parameter override."""
    tool = GreetTool()
    result = await tool.execute({"name": "World", "greeting": "Hi"}, _ctx())
    assert result.output == "Hi, World!"


@pytest.mark.asyncio
async def test_callable_tool_validation_error():
    """Missing required parameter raises ToolValidateError."""
    tool = GreetTool()
    with pytest.raises(ToolValidateError) as exc_info:
        await tool.execute({}, _ctx())
    assert exc_info.value.tool_id == "greet"
    assert len(exc_info.value.errors) > 0


@pytest.mark.asyncio
async def test_callable_tool_wrong_type():
    """Wrong type parameter raises ToolValidateError."""
    tool = NumberTool()
    with pytest.raises(ToolValidateError):
        await tool.execute({"value": "not_a_number"}, _ctx())


def test_callable_tool_auto_schema():
    """Schema is auto-generated from Pydantic model."""
    tool = GreetTool()
    schema = tool.parameters_schema()
    assert schema["type"] == "object"
    assert "name" in schema["properties"]
    assert "greeting" in schema["properties"]
    assert "name" in schema["required"]
    # greeting has default, so not in required
    assert "greeting" not in schema.get("required", [])


def test_callable_tool_schema_descriptions():
    """Property descriptions come from Pydantic Field."""
    tool = GreetTool()
    schema = tool.parameters_schema()
    assert schema["properties"]["name"]["description"] == "Name to greet"


def test_callable_tool_to_llm_tool():
    """to_llm_tool produces correct format."""
    tool = GreetTool()
    llm = tool.to_llm_tool()
    assert llm["type"] == "function"
    assert llm["function"]["name"] == "greet"
    assert llm["function"]["description"] == "A test greeting tool"
    assert "properties" in llm["function"]["parameters"]


def test_validate_args_returns_typed_params():
    """validate_args returns the correct Pydantic model."""
    tool = GreetTool()
    params = tool.validate_args({"name": "Test"})
    assert isinstance(params, GreetParams)
    assert params.name == "Test"
    assert params.greeting == "Hello"  # default


# ── ToolResult / ToolOk / ToolError ───────────────────────────────────


def test_tool_ok_defaults():
    r = ToolOk("success")
    assert r.output == "success"
    assert not r.is_error
    assert r.message == ""
    assert r.display == ""
    assert r.metadata == {}


def test_tool_error_defaults():
    r = ToolError("failure")
    assert r.output == "failure"
    assert r.is_error
    assert r.message == ""


def test_tool_ok_with_all_fields():
    r = ToolOk("out", message="msg", display="disp", title="t", metadata={"k": "v"})
    assert r.output == "out"
    assert r.message == "msg"
    assert r.display == "disp"
    assert r.title == "t"
    assert r.metadata == {"k": "v"}
    assert not r.is_error


def test_tool_error_with_all_fields():
    r = ToolError("out", message="msg", display="disp", title="t", metadata={"k": "v"})
    assert r.is_error
    assert r.title == "t"


def test_tool_result_is_error_flag():
    """ToolResult directly constructed should have configurable is_error."""
    r = ToolResult(output="x", is_error=True)
    assert r.is_error
    r2 = ToolResult(output="x", is_error=False)
    assert not r2.is_error


# ── Error hierarchy ───────────────────────────────────────────────────


def test_tool_not_found_error():
    e = ToolNotFoundError("xyz")
    assert e.tool_id == "xyz"
    assert "Unknown tool" in str(e)


def test_tool_parse_error():
    e = ToolParseError("bash", "invalid{json", ValueError("bad"))
    assert e.tool_id == "bash"
    assert "Failed to parse" in str(e)


def test_tool_validate_error():
    e = ToolValidateError("edit", [{"loc": ("file_path",), "msg": "required"}])
    assert e.tool_id == "edit"
    assert "Validation failed" in str(e)


def test_tool_runtime_error():
    cause = RuntimeError("disk full")
    e = ToolRuntimeError("write", cause)
    assert e.tool_id == "write"
    assert e.cause is cause
    assert "runtime error" in str(e)


def test_error_hierarchy_is_consistent():
    """All tool errors inherit from ToolBaseError."""
    from opencode.tool.base import ToolBaseError
    assert issubclass(ToolNotFoundError, ToolBaseError)
    assert issubclass(ToolParseError, ToolBaseError)
    assert issubclass(ToolValidateError, ToolBaseError)
    assert issubclass(ToolRuntimeError, ToolBaseError)


# ── ToolResultBuilder ─────────────────────────────────────────────────


def test_builder_basic():
    b = ToolResultBuilder()
    b.add("hello")
    b.add(" world")
    assert b.build() == "hello world"
    assert len(b) == 11


def test_builder_truncation():
    b = ToolResultBuilder(max_chars=20)
    b.add("a" * 30)
    result = b.build()
    assert b.truncated
    assert "truncated" in result
    assert len(result.split("\n")[0]) <= 20


def test_builder_line_truncation():
    b = ToolResultBuilder(max_line_len=10)
    b.add("short\n" + "x" * 50)
    result = b.build()
    assert "line truncated" in result


def test_builder_heading():
    b = ToolResultBuilder()
    b.add_heading("Section")
    b.add("content")
    result = b.build()
    assert "--- Section ---" in result
    assert "content" in result


def test_builder_chaining():
    b = ToolResultBuilder()
    result = b.add("a").add("b").add("c").build()
    assert result == "abc"


def test_builder_stops_after_truncation():
    b = ToolResultBuilder(max_chars=5)
    b.add("12345")  # exactly at limit
    b.add("more")   # should be ignored
    result = b.build()
    assert "more" not in result or "truncated" in result


# ── Description template loading ──────────────────────────────────────


def test_load_description_existing():
    desc = load_description("bash")
    assert "shell command" in desc.lower()


def test_load_description_nonexistent():
    desc = load_description("nonexistent_tool_xyz")
    assert desc == ""


def test_load_description_all_tools():
    """All built-in tools should have description templates."""
    tool_ids = ["bash", "read", "edit", "write", "grep", "glob", "task", "webfetch", "websearch", "question", "todo", "skill", "batch"]
    for tid in tool_ids:
        desc = load_description(tid)
        assert desc, f"Missing description template for {tid}"


# ── Registry enhancements ─────────────────────────────────────────────


@pytest.fixture
def clean_registry():
    """Clean registry for registry tests."""
    registry.clear()
    yield
    registry.clear()


def test_registry_hide_unhide(clean_registry):
    tool = GreetTool()
    registry.register(tool)
    assert len(registry.visible_tools()) == 1

    registry.hide("greet")
    assert registry.is_hidden("greet")
    assert len(registry.visible_tools()) == 0
    # Still in all_tools
    assert len(registry.all_tools()) == 1

    registry.unhide("greet")
    assert not registry.is_hidden("greet")
    assert len(registry.visible_tools()) == 1


def test_registry_to_llm_tools_excludes_hidden(clean_registry):
    tool = GreetTool()
    registry.register(tool)
    assert len(registry.to_llm_tools()) == 1

    registry.hide("greet")
    assert len(registry.to_llm_tools()) == 0


def test_registry_get_or_raise(clean_registry):
    tool = GreetTool()
    registry.register(tool)
    assert registry.get_or_raise("greet") is tool

    with pytest.raises(ToolNotFoundError):
        registry.get_or_raise("nonexistent")


def test_registry_unregister(clean_registry):
    tool = GreetTool()
    registry.register(tool)
    assert registry.get("greet") is not None

    registry.unregister("greet")
    assert registry.get("greet") is None


def test_registry_clear(clean_registry):
    tool = GreetTool()
    registry.register(tool)
    registry.hide("greet")

    registry.clear()
    assert len(registry.all_tools()) == 0
    assert not registry.is_hidden("greet")


def test_registry_builtins_idempotent(clean_registry):
    registry.register_builtins()
    count1 = len(registry.all_tools())
    registry.register_builtins()  # Second call should be no-op
    count2 = len(registry.all_tools())
    assert count1 == count2


# ── Backward compatibility ────────────────────────────────────────────


def test_old_tool_info_still_works():
    """ToolInfo (non-generic) should still work for backward compatibility."""
    from typing import Any

    class LegacyTool(ToolInfo):
        id = "legacy"
        description = "A legacy tool"

        def parameters_schema(self) -> dict[str, Any]:
            return {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}

        async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
            return ToolOk(args["x"], title="Legacy")

    tool = LegacyTool()
    assert tool.id == "legacy"
    schema = tool.parameters_schema()
    assert "x" in schema["properties"]

    llm = tool.to_llm_tool()
    assert llm["function"]["name"] == "legacy"
