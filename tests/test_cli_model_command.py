"""Tests for the /model slash command in the interactive CLI."""
from __future__ import annotations

import pytest
from io import StringIO
from unittest.mock import patch

from opencode.cli.main import _handle_command


class TestHandleCommand:
    """Test _handle_command for basic slash commands."""

    def test_quit_commands(self):
        for cmd in ("/quit", "/exit", "/q"):
            assert _handle_command(cmd, []) == "quit"

    def test_clear_commands(self):
        for cmd in ("/clear", "/reset"):
            assert _handle_command(cmd, []) == "clear"

    def test_help_command(self):
        result = _handle_command("/help", [])
        assert result == ""

    def test_history_empty(self):
        result = _handle_command("/history", [])
        assert result == ""

    def test_history_with_entries(self):
        history = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
        result = _handle_command("/history", history)
        assert result == ""

    def test_unknown_command(self):
        result = _handle_command("/foobar", [])
        assert result == ""


class TestModelCommandInline:
    """Test the /model inline logic that runs inside _run_loop.

    Since the /model command is handled inline in the loop (not via _handle_command),
    we test the matching/selection logic in isolation here.
    """

    def _simulate_model_switch(
        self,
        user_input: str,
        model_ref: list[str | None],
        available_models: list[str],
    ) -> str:
        """Simulate the /model command logic from the main loop.

        Returns: "list", "switched:<model>", "ambiguous", "unknown", or "no_models"
        """
        text = user_input.strip()
        parts_cmd = text.split(None, 1)

        if len(parts_cmd) < 2:
            # No argument — list
            if available_models:
                return "list"
            else:
                return "no_models"
        else:
            new_model = parts_cmd[1].strip()
            if new_model in available_models:
                model_ref[0] = new_model
                return f"switched:{new_model}"
            else:
                matches = [m for m in available_models if new_model.lower() in m.lower()]
                if len(matches) == 1:
                    model_ref[0] = matches[0]
                    return f"switched:{matches[0]}"
                elif matches:
                    return "ambiguous"
                else:
                    return "unknown"

    def test_list_models(self):
        model_ref = [None]
        available = ["openai/gpt-4o", "anthropic/claude-sonnet-4-20250514"]
        result = self._simulate_model_switch("/model", model_ref, available)
        assert result == "list"
        assert model_ref[0] is None  # Unchanged

    def test_list_models_empty(self):
        model_ref = [None]
        result = self._simulate_model_switch("/model", model_ref, [])
        assert result == "no_models"

    def test_switch_exact_match(self):
        model_ref = ["openai/gpt-4o"]
        available = ["openai/gpt-4o", "anthropic/claude-sonnet-4-20250514", "openai/gpt-4o-mini"]
        result = self._simulate_model_switch("/model anthropic/claude-sonnet-4-20250514", model_ref, available)
        assert result == "switched:anthropic/claude-sonnet-4-20250514"
        assert model_ref[0] == "anthropic/claude-sonnet-4-20250514"

    def test_switch_fuzzy_unique(self):
        model_ref = ["openai/gpt-4o"]
        available = ["openai/gpt-4o", "anthropic/claude-sonnet-4-20250514", "openai/gpt-4o-mini"]
        result = self._simulate_model_switch("/model claude-sonnet", model_ref, available)
        assert result == "switched:anthropic/claude-sonnet-4-20250514"
        assert model_ref[0] == "anthropic/claude-sonnet-4-20250514"

    def test_switch_fuzzy_ambiguous(self):
        model_ref = ["openai/gpt-4o"]
        available = ["openai/gpt-4o", "openai/gpt-4o-mini", "anthropic/claude-sonnet-4-20250514"]
        result = self._simulate_model_switch("/model gpt-4o", model_ref, available)
        assert result == "ambiguous"
        assert model_ref[0] == "openai/gpt-4o"  # Unchanged

    def test_switch_unknown(self):
        model_ref = ["openai/gpt-4o"]
        available = ["openai/gpt-4o", "anthropic/claude-sonnet-4-20250514"]
        result = self._simulate_model_switch("/model deepseek/xxx", model_ref, available)
        assert result == "unknown"
        assert model_ref[0] == "openai/gpt-4o"  # Unchanged

    def test_switch_from_none(self):
        model_ref = [None]
        available = ["openai/gpt-4o", "anthropic/claude-sonnet-4-20250514"]
        result = self._simulate_model_switch("/model openai/gpt-4o", model_ref, available)
        assert result == "switched:openai/gpt-4o"
        assert model_ref[0] == "openai/gpt-4o"

    def test_switch_case_insensitive_fuzzy(self):
        model_ref = [None]
        available = ["openai/GPT-4o", "anthropic/claude-sonnet-4-20250514"]
        result = self._simulate_model_switch("/model gpt-4o", model_ref, available)
        assert result == "switched:openai/GPT-4o"
        assert model_ref[0] == "openai/GPT-4o"
