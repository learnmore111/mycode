"""New test file."""
import pytest


def test_addition():
    assert 2 + 2 == 4


def test_list_append():
    lst = [1, 2, 3]
    lst.append(4)
    assert lst == [1, 2, 3, 4]


def test_dict_get():
    d = {"key": "value"}
    assert d.get("key") == "value"
    assert d.get("missing", "default") == "default"
