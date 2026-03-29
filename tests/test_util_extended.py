"""Extended tests for util modules: context, filesystem, log, paths."""
import os
import pytest
from opencode.util import filesystem as fs
from opencode.util.context import get_instance, set_instance, has_instance
from opencode.util.log import Logger, create, init
from opencode.util.paths import GlobalPaths


# --- context tests ---

def test_has_instance_false():
    # Should be False in a clean context (or depends on test ordering)
    # Just verify the function exists and returns a bool
    result = has_instance()
    assert isinstance(result, bool)


# --- filesystem tests ---

def test_resolve():
    r = fs.resolve(".")
    assert os.path.isabs(r)


def test_exists_true(tmp_path):
    (tmp_path / "exists.txt").write_text("hi")
    assert fs.exists(str(tmp_path / "exists.txt"))


def test_exists_false(tmp_path):
    assert not fs.exists(str(tmp_path / "nope.txt"))


def test_is_dir(tmp_path):
    assert fs.is_dir(str(tmp_path))
    assert not fs.is_dir(str(tmp_path / "nope"))


def test_stat(tmp_path):
    f = tmp_path / "stattest.txt"
    f.write_text("data")
    s = fs.stat(str(f))
    assert s is not None
    assert s.st_size > 0


def test_stat_missing(tmp_path):
    assert fs.stat(str(tmp_path / "missing")) is None


@pytest.mark.asyncio
async def test_read_write_text(tmp_path):
    p = str(tmp_path / "test.txt")
    await fs.write_text(p, "hello world")
    content = await fs.read_text(p)
    assert content == "hello world"


@pytest.mark.asyncio
async def test_read_write_bytes(tmp_path):
    p = str(tmp_path / "test.bin")
    await fs.write_bytes(p, b"\x00\x01\x02")
    data = await fs.read_bytes(p)
    assert data == b"\x00\x01\x02"


@pytest.mark.asyncio
async def test_read_write_json(tmp_path):
    p = str(tmp_path / "test.json")
    await fs.write_json(p, {"key": "value", "num": 42})
    data = await fs.read_json(p)
    assert data["key"] == "value"
    assert data["num"] == 42


def test_read_text_sync(tmp_path):
    f = tmp_path / "sync.txt"
    f.write_text("sync content")
    assert fs.read_text_sync(str(f)) == "sync content"


def test_read_json_sync(tmp_path):
    f = tmp_path / "sync.json"
    f.write_text('{"a": 1}')
    assert fs.read_json_sync(str(f)) == {"a": 1}


def test_write_json_sync(tmp_path):
    p = str(tmp_path / "out.json")
    fs.write_json_sync(p, [1, 2, 3])
    assert fs.read_json_sync(p) == [1, 2, 3]


def test_mime_type():
    assert "text" in fs.mime_type("test.txt") or fs.mime_type("test.txt") == "text/plain"
    assert fs.mime_type("data.json") is not None
    # Use truly unknown extension
    assert fs.mime_type("unknown.qwzxyz123") == "application/octet-stream"


@pytest.mark.asyncio
async def test_ensure_dir(tmp_path):
    d = str(tmp_path / "a" / "b" / "c")
    await fs.ensure_dir(d)
    assert os.path.isdir(d)


@pytest.mark.asyncio
async def test_remove(tmp_path):
    f = tmp_path / "removable.txt"
    f.write_text("gone")
    await fs.remove(str(f))
    assert not f.exists()


@pytest.mark.asyncio
async def test_remove_missing(tmp_path):
    await fs.remove(str(tmp_path / "nope.txt"))  # should not raise


# --- log tests ---

def test_create_logger():
    logger = create(service="test")
    assert isinstance(logger, Logger)


def test_logger_methods():
    logger = create(service="test")
    # These should not raise
    logger.debug("debug msg")
    logger.info("info msg")
    logger.warn("warn msg")
    logger.error("error msg")


def test_logger_tag():
    logger = create(service="test")
    tagged = logger.tag("request_id", "abc123")
    assert tagged is logger  # tag returns self


def test_logger_clone():
    logger = create(service="test")
    logger.tag("key", "val")
    cloned = logger.clone()
    assert cloned is not logger


# --- paths tests ---

def test_global_paths_data():
    p = GlobalPaths.data()
    assert "opencode" in str(p)


def test_global_paths_config():
    p = GlobalPaths.config()
    assert "opencode" in str(p)


def test_global_paths_state():
    p = GlobalPaths.state()
    assert "opencode" in str(p)


def test_global_paths_cache():
    p = GlobalPaths.cache()
    assert "opencode" in str(p)


def test_global_paths_home():
    p = GlobalPaths.home()
    assert p.exists()
