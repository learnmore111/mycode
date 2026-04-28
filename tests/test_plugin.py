"""Tests for the plugin system."""
import pytest
from mycode.plugin.plugin import PluginManager


@pytest.mark.asyncio
async def test_init_empty():
    pm = PluginManager()
    await pm.init(None)
    assert pm.list_plugins() == []


@pytest.mark.asyncio
async def test_init_nonexistent_module():
    pm = PluginManager()
    await pm.init(["nonexistent_module_xyz"])
    plugins = pm.list_plugins()
    assert len(plugins) == 1
    assert plugins[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_trigger_no_hooks():
    pm = PluginManager()
    result = await pm.trigger("some_hook", {"input": 1}, {"output": 2})
    assert result == {"output": 2}


@pytest.mark.asyncio
async def test_register_and_trigger():
    pm = PluginManager()
    called = []

    async def my_hook(inp, out):
        called.append((inp, out))

    pm._register_hooks({"before_tool": my_hook})
    await pm.trigger("before_tool", "in", "out")
    assert len(called) == 1
    assert called[0] == ("in", "out")


def test_list_plugins_empty():
    pm = PluginManager()
    assert pm.list_plugins() == []
