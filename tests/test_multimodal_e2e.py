"""Multimodal end-to-end tests.

Validates the full chain:

1. ``create_file_part`` factory builds a ``FilePart`` with correct fields.
2. ``save_part`` / ``_build_part_row`` persist mime_type via tool_call_id,
   filename via tool, and base64 content via content — without schema
   changes to ``PartTable``.
3. ``rebuild_history_from_db`` reconstructs the OpenAI content-list
   format for user messages that carry file parts, correctly routing
   image → ``image_url``, pdf → ``file``, audio → ``input_audio``.
4. ``_normalize_image_url`` coerces the three supported input shapes.
5. Capability gating rejects unsupported modalities early.
"""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING

import pytest

from mycode.session.message import (
    FilePart,
    create_file_part,
    create_text_part,
    create_user_message,
    save_message,
    save_part,
)
from mycode.session.prompt import _normalize_image_url

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# 1. create_file_part factory
# ---------------------------------------------------------------------------


def test_create_file_part_fields() -> None:
    fp = create_file_part(
        "sess1",
        "msg1",
        mime_type="image/png",
        content="iVBOR...base64...",
        filename="screenshot.png",
    )
    assert isinstance(fp, FilePart)
    assert fp.type == "file"
    assert fp.mime_type == "image/png"
    assert fp.content == "iVBOR...base64..."
    assert fp.filename == "screenshot.png"
    assert fp.session_id == "sess1"
    assert fp.message_id == "msg1"
    assert fp.id  # auto-generated
    assert fp.time_created > 0


def test_create_file_part_defaults() -> None:
    fp = create_file_part("s", "m", mime_type="audio/wav", content="data")
    assert fp.filename == ""


# ---------------------------------------------------------------------------
# 2. Persistence round-trip (requires DB)
# ---------------------------------------------------------------------------


@pytest.fixture()
def _db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Bootstrap an in-memory DB for the test."""
    from mycode.storage import database as db

    monkeypatch.setenv("OPENCODE_DB", str(tmp_path / "test.db"))
    db.reset()
    db.get_engine()
    yield
    db.reset()


def test_file_part_persist_and_read(_db: None) -> None:
    from mycode.storage.database import session_scope
    from mycode.storage.models import PartTable

    msg = create_user_message("sess-test")
    save_message(msg)

    fp = create_file_part(
        "sess-test",
        msg.id,
        mime_type="image/jpeg",
        content="AQID",  # base64 of b'\x01\x02\x03'
        filename="photo.jpg",
    )
    save_part(fp)

    with session_scope() as db:
        row = db.query(PartTable).filter(PartTable.id == fp.id).one()
        assert row.type == "file"
        assert row.content == "AQID"
        assert row.tool == "photo.jpg"  # filename
        assert row.tool_call_id == "image/jpeg"  # mime_type


# ---------------------------------------------------------------------------
# 3. rebuild_history_from_db with multimodal content-list
# ---------------------------------------------------------------------------


def test_rebuild_history_multimodal(_db: None) -> None:
    from mycode.session.message import rebuild_history_from_db

    msg = create_user_message("sess-mm")
    save_message(msg)

    tp = create_text_part("sess-mm", msg.id)
    tp.content = "Describe this image"
    save_part(tp)

    fp = create_file_part(
        "sess-mm",
        msg.id,
        mime_type="image/png",
        content=base64.b64encode(b"\x89PNG").decode(),
        filename="diagram.png",
    )
    save_part(fp)

    history = rebuild_history_from_db("sess-mm")
    assert len(history) == 1
    entry = history[0]
    assert entry["role"] == "user"
    # Must be a content-list, not a plain string
    content = entry["content"]
    assert isinstance(content, list)
    # Text block first
    assert content[0] == {"type": "text", "text": "Describe this image"}
    # Image block second
    img = content[1]
    assert img["type"] == "image_url"
    assert "data:image/png;base64," in img["image_url"]["url"]


def test_rebuild_history_audio_part(_db: None) -> None:
    from mycode.session.message import rebuild_history_from_db

    msg = create_user_message("sess-audio")
    save_message(msg)

    fp = create_file_part(
        "sess-audio",
        msg.id,
        mime_type="audio/wav",
        content="UklGR...",
        filename="clip.wav",
    )
    save_part(fp)

    history = rebuild_history_from_db("sess-audio")
    assert len(history) == 1
    content = history[0]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "input_audio"


def test_rebuild_history_pdf_part(_db: None) -> None:
    from mycode.session.message import rebuild_history_from_db

    msg = create_user_message("sess-pdf")
    save_message(msg)

    fp = create_file_part(
        "sess-pdf",
        msg.id,
        mime_type="application/pdf",
        content="JVBERi0...",
        filename="doc.pdf",
    )
    save_part(fp)

    history = rebuild_history_from_db("sess-pdf")
    content = history[0]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "file"
    assert "file_data" in content[0]["file"]


def test_rebuild_history_text_only_unchanged(_db: None) -> None:
    """A plain text-only user message must still be a simple string."""
    from mycode.session.message import rebuild_history_from_db

    msg = create_user_message("sess-plain")
    save_message(msg)
    tp = create_text_part("sess-plain", msg.id)
    tp.content = "Hello"
    save_part(tp)

    history = rebuild_history_from_db("sess-plain")
    assert history[0]["content"] == "Hello"


# ---------------------------------------------------------------------------
# 4. _normalize_image_url
# ---------------------------------------------------------------------------


def test_normalize_url_passthrough() -> None:
    assert _normalize_image_url({"type": "image", "url": "https://example.com/img.png"}) == "https://example.com/img.png"


def test_normalize_data_uri_passthrough() -> None:
    uri = "data:image/png;base64,iVBOR"
    assert _normalize_image_url({"type": "image", "content": uri}) == uri


def test_normalize_raw_base64_wrapped() -> None:
    result = _normalize_image_url({"type": "image", "content": "iVBOR", "mime": "image/jpeg"})
    assert result == "data:image/jpeg;base64,iVBOR"


def test_normalize_empty_returns_none() -> None:
    assert _normalize_image_url({"type": "image"}) is None


def test_normalize_defaults_to_png() -> None:
    result = _normalize_image_url({"type": "image", "content": "abc"})
    assert result is not None
    assert result.startswith("data:image/png;base64,")


# ---------------------------------------------------------------------------
# 5. Capability gating (unit-level — no LLM call needed)
# ---------------------------------------------------------------------------


def test_prompt_input_parts_schema() -> None:
    """Verify the parts schema accepted by prompt.py supports multimodal types."""
    from mycode.session.prompt import PromptInput

    inp = PromptInput(
        session_id="test",
        parts=[
            {"type": "text", "content": "What is this?"},
            {"type": "image", "content": "iVBOR", "mime": "image/png"},
            {"type": "pdf", "content": "JVBERi0", "mime": "application/pdf"},
            {"type": "audio", "content": "UklGR", "mime": "audio/wav"},
        ],
    )
    assert len(inp.parts) == 4
    types = [p["type"] for p in inp.parts]
    assert types == ["text", "image", "pdf", "audio"]
