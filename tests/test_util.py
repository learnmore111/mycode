"""Tests for utility modules."""

from mycode.util import hash as hashmod
from mycode.util import wildcard
from mycode.util import ids
from mycode.util import slug
from mycode.util.error import NamedError, NotFoundError


def test_hash_fast():
    h = hashmod.fast("hello")
    assert len(h) == 32
    assert h == hashmod.fast("hello")
    assert h != hashmod.fast("world")


def test_wildcard_match():
    assert wildcard.match("bash", "*")
    assert wildcard.match("bash", "bash")
    assert wildcard.match("bash", "bas*")
    assert not wildcard.match("bash", "read")
    assert wildcard.match("path/to/file.ts", "*.ts")
    assert wildcard.match("path/to/file.ts", "path/*")


def test_wildcard_match_any():
    assert wildcard.match_any("bash", ["read", "bash", "edit"])
    assert not wildcard.match_any("bash", ["read", "edit"])


def test_ids_ascending():
    a = ids.ascending()
    b = ids.ascending()
    assert a < b  # ULIDs are time-ordered


def test_ids_message():
    mid = ids.message_id()
    assert len(mid) == 26  # ULID length


def test_slug_create():
    s = slug.create()
    assert len(s) == 8
    assert s.isalnum()


def test_named_error():
    err = NotFoundError({"path": "/foo"}, message="not found")
    assert err.name == "NotFoundError"
    assert err.data["path"] == "/foo"
    d = err.to_dict()
    assert d["name"] == "NotFoundError"


def test_named_error_factory():
    MyError = NamedError.create("MyCustomError")
    err = MyError({"key": "val"})
    assert err.name == "MyCustomError"
