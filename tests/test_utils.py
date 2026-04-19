"""Utility function tests."""
import pytest


def test_list_operations():
    data = [1, 2, 3, 4, 5]
    assert sum(data) == 15
    assert len(data) == 5


def test_dict_operations():
    user = {"name": "Alice", "age": 30}
    assert user["name"] == "Alice"
    assert "age" in user


@pytest.mark.parametrize("value,expected", [(1, True), (0, False), (-1, True)])
def test_truthiness(value, expected):
    assert bool(value) == expected


class TestClass:
    def test_method(self):
        assert "test".lower() == "test"
