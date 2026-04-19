"""Tests for JSON storage and storage models."""
import pytest
from mycode.storage.json_storage import read, write, remove, list_keys, exists
from mycode.storage.models import SessionTable, MessageTable, PartTable


@pytest.fixture(autouse=True)
def _use_tmp_storage(tmp_path, monkeypatch):
    monkeypatch.setattr("mycode.util.paths.GlobalPaths.data", staticmethod(lambda: tmp_path))


@pytest.mark.asyncio
async def test_write_and_read():
    await write(["test", "key1"], {"value": 42})
    data = await read(["test", "key1"])
    assert data["value"] == 42


@pytest.mark.asyncio
async def test_read_missing():
    with pytest.raises(FileNotFoundError):
        await read(["nonexistent", "key"])


@pytest.mark.asyncio
async def test_remove_key():
    await write(["test", "removable"], {"data": True})
    assert exists(["test", "removable"])
    await remove(["test", "removable"])
    assert not exists(["test", "removable"])


@pytest.mark.asyncio
async def test_list_keys():
    await write(["ns", "a"], 1)
    await write(["ns", "b"], 2)
    keys = await list_keys(["ns"])
    assert len(keys) == 2
    flat = ["/".join(k) for k in keys]
    assert "ns/a" in flat
    assert "ns/b" in flat


@pytest.mark.asyncio
async def test_list_keys_empty():
    keys = await list_keys(["empty_ns"])
    assert keys == []


def test_exists_false():
    assert exists(["nope"]) is False


# --- ORM model tests ---

def test_session_table_columns():
    assert SessionTable.__tablename__ == "session"
    assert hasattr(SessionTable, "id")
    assert hasattr(SessionTable, "project_id")
    assert hasattr(SessionTable, "title")


def test_message_table_columns():
    assert MessageTable.__tablename__ == "message"
    assert hasattr(MessageTable, "role")
    assert hasattr(MessageTable, "session_id")


def test_part_table_columns():
    assert PartTable.__tablename__ == "part"
    assert hasattr(PartTable, "type")
    assert hasattr(PartTable, "tool")
    assert hasattr(PartTable, "tool_call_id")
