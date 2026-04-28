"""Tests for create_skill tool."""
import tempfile
from pathlib import Path

import pytest

from mycode.project.instance import provide
from mycode.tool.create_skill import CreateSkillParams, CreateSkillTool


@pytest.mark.asyncio
async def test_create_skill_basic():
    """Test basic skill creation."""
    tool = CreateSkillTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        async def _create_skill():
            params = CreateSkillParams(
                name="test-skill",
                content="# Test Skill\n\nThis is a test skill.",
                scope="project"
            )

            result = await tool.call(params, ctx=None)  # type: ignore

            assert not result.is_error
            assert "Created skill 'test-skill'" in result.output
            assert ".mycode/skills/test-skill.md" in result.output

            # Verify file was created
            skill_path = Path(tmpdir) / ".mycode" / "skills" / "test-skill.md"
            assert skill_path.exists()
            assert skill_path.read_text() == "# Test Skill\n\nThis is a test skill."

        await provide(tmpdir, _create_skill)


@pytest.mark.asyncio
async def test_create_skill_global():
    """Test global skill creation."""
    tool = CreateSkillTool()

    params = CreateSkillParams(
        name="global-test-skill",
        content="# Global Skill",
        scope="global"
    )

    result = await tool.call(params, ctx=None)  # type: ignore

    assert not result.is_error

    # Verify file was created in home directory
    skill_path = Path.home() / ".mycode" / "skills" / "global-test-skill.md"
    assert skill_path.exists()

    # Cleanup
    skill_path.unlink()


@pytest.mark.asyncio
async def test_create_skill_invalid_name():
    """Test skill creation with invalid name."""
    tool = CreateSkillTool()

    params = CreateSkillParams(
        name="invalid@name!",
        content="# Skill",
        scope="project"
    )

    result = await tool.call(params, ctx=None)  # type: ignore

    assert result.is_error
    assert "Invalid skill name" in result.output


@pytest.mark.asyncio
async def test_create_skill_empty_content():
    """Test skill creation with empty content."""
    tool = CreateSkillTool()

    params = CreateSkillParams(
        name="empty-skill",
        content="   ",
        scope="project"
    )

    result = await tool.call(params, ctx=None)  # type: ignore

    assert result.is_error
    assert "cannot be empty" in result.output


@pytest.mark.asyncio
async def test_create_skill_overwrite():
    """Test overwriting existing skill."""
    tool = CreateSkillTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        async def _overwrite_skill():
            # Create initial skill
            params1 = CreateSkillParams(
                name="overwrite-test",
                content="# Version 1",
                scope="project"
            )
            result1 = await tool.call(params1, ctx=None)  # type: ignore
            assert not result1.is_error
            assert "Created skill" in result1.output

            # Overwrite
            params2 = CreateSkillParams(
                name="overwrite-test",
                content="# Version 2\n\nUpdated content.",
                scope="project"
            )
            result2 = await tool.call(params2, ctx=None)  # type: ignore

            assert not result2.is_error
            assert "Overwrote skill" in result2.output

            # Verify content was updated
            skill_path = Path(tmpdir) / ".mycode" / "skills" / "overwrite-test.md"
            assert skill_path.read_text() == "# Version 2\n\nUpdated content."

        await provide(tmpdir, _overwrite_skill)


@pytest.mark.asyncio
async def test_create_skill_metadata():
    """Test that metadata is correct."""
    tool = CreateSkillTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        async def _check_metadata():
            params = CreateSkillParams(
                name="metadata-test",
                content="# Test\n\nLine 2\nLine 3",
                scope="project"
            )

            result = await tool.call(params, ctx=None)  # type: ignore

            assert not result.is_error
            assert result.metadata["success"] is True
            assert result.metadata["name"] == "metadata-test"
            assert result.metadata["scope"] == "project"
            assert result.metadata["lines"] == 4
            assert result.metadata["created"] is True

        await provide(tmpdir, _check_metadata)


def test_tool_capabilities():
    """Test tool capability declarations."""
    tool = CreateSkillTool()

    assert tool.is_read_only() is False
    assert tool.is_destructive() is False
    assert tool.is_concurrency_safe() is True
