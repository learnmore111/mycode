"""Tests for the auth system."""
import pytest
from mycode.auth.auth import ApiKeyAuth, OAuthAuth, get, set_, remove, all_


@pytest.fixture(autouse=True)
def _use_tmp_data(tmp_path, monkeypatch):
    """Redirect GlobalPaths.data() to a temp dir."""
    monkeypatch.setattr("mycode.util.paths.GlobalPaths.data", staticmethod(lambda: tmp_path))


@pytest.mark.asyncio
async def test_set_and_get():
    info = ApiKeyAuth(type="api", key="sk-test-123")
    await set_("test-provider", info)
    loaded = await get("test-provider")
    assert loaded is not None
    assert loaded.type == "api"
    assert loaded.key == "sk-test-123"


@pytest.mark.asyncio
async def test_get_missing():
    result = await get("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_remove():
    await set_("removable", ApiKeyAuth(type="api", key="key123"))
    await remove("removable")
    assert await get("removable") is None


@pytest.mark.asyncio
async def test_all():
    await set_("prov1", ApiKeyAuth(type="api", key="k1"))
    await set_("prov2", ApiKeyAuth(type="api", key="k2"))
    result = await all_()
    assert "prov1" in result
    assert "prov2" in result
    assert result["prov1"].key == "k1"


@pytest.mark.asyncio
async def test_oauth_auth():
    info = OAuthAuth(type="oauth", access="tok", refresh="ref", expires=9999)
    await set_("oauth-prov", info)
    loaded = await get("oauth-prov")
    assert loaded is not None
    assert loaded.type == "oauth"
    assert loaded.access == "tok"
