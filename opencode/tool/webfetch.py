"""WebFetch tool — fetch and extract content from URLs.

Features:
- JSON content-type detection and formatting
- XML content-type passthrough
- Capability declarations (is_read_only=True)
"""
from __future__ import annotations

import html
import re
from typing import Any

import httpx
from pydantic import BaseModel, Field

from opencode.tool.base import CallableTool, ToolContext, ToolError, ToolOk, ToolResult, ToolResultBuilder


def _html_to_markdown(raw: str) -> str:
    """Convert HTML to readable markdown-like text, preserving structure.

    Handles headings, links, code blocks, lists, and paragraphs.
    Falls back gracefully — better than pure tag stripping.
    """
    text = raw

    # Remove script, style, nav, footer, header
    for tag in ("script", "style", "nav", "footer", "header", "aside"):
        text = re.sub(rf"<{tag}[^>]*>.*?</{tag}>", "", text, flags=re.DOTALL | re.IGNORECASE)

    # Headings → markdown
    for level in range(1, 7):
        text = re.sub(
            rf"<h{level}[^>]*>(.*?)</h{level}>",
            lambda m, lv=level: f"\n\n{'#' * lv} {_strip_tags(m.group(1)).strip()}\n",
            text, flags=re.DOTALL | re.IGNORECASE,
        )

    # Code blocks: <pre><code>...</code></pre>
    text = re.sub(
        r"<pre[^>]*>\s*<code[^>]*>(.*?)</code>\s*</pre>",
        lambda m: f"\n```\n{html.unescape(_strip_tags(m.group(1)))}\n```\n",
        text, flags=re.DOTALL | re.IGNORECASE,
    )
    # Inline code
    text = re.sub(r"<code[^>]*>(.*?)</code>", lambda m: f"`{_strip_tags(m.group(1))}`", text, flags=re.DOTALL | re.IGNORECASE)

    # Links: <a href="url">text</a> → [text](url)
    text = re.sub(
        r'<a[^>]+href="([^"]*)"[^>]*>(.*?)</a>',
        lambda m: f"[{_strip_tags(m.group(2)).strip()}]({m.group(1)})" if m.group(1).startswith("http") else _strip_tags(m.group(2)),
        text, flags=re.DOTALL | re.IGNORECASE,
    )

    # List items
    text = re.sub(r"<li[^>]*>(.*?)</li>", lambda m: f"\n- {_strip_tags(m.group(1)).strip()}", text, flags=re.DOTALL | re.IGNORECASE)

    # Paragraphs and divs → double newline
    text = re.sub(r"<(?:p|div|article|section|blockquote)[^>]*>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(?:p|div|article|section|blockquote)>", "\n", text, flags=re.IGNORECASE)

    # Line breaks
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)

    # Bold / italic
    text = re.sub(r"<(?:b|strong)[^>]*>(.*?)</(?:b|strong)>", r"**\1**", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<(?:i|em)[^>]*>(.*?)</(?:i|em)>", r"*\1*", text, flags=re.DOTALL | re.IGNORECASE)

    # Strip remaining tags
    text = _strip_tags(text)

    # Decode HTML entities
    text = html.unescape(text)

    # Clean up excessive whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    # Clean up lines
    lines = [line.strip() for line in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def _strip_tags(text: str) -> str:
    """Remove all HTML tags."""
    return re.sub(r"<[^>]+>", "", text)


class WebFetchParams(BaseModel):
    """Parameters for the webfetch tool."""
    url: str = Field(description="The URL to fetch content from")
    extract: str | None = Field(default=None, description="What information to extract from the page")


class WebFetchTool(CallableTool[WebFetchParams]):
    id = "webfetch"
    description = "Fetch content from a URL. Returns the page content as markdown-formatted text. HTTP URLs are upgraded to HTTPS."

    def is_read_only(self, args: dict[str, Any] | None = None) -> bool:
        return True

    def is_concurrency_safe(self, args: dict[str, Any] | None = None) -> bool:
        return True

    async def call(self, params: WebFetchParams, ctx: ToolContext) -> ToolResult:
        url = params.url
        if url.startswith("http://"):
            url = "https://" + url[7:]

        if not url.startswith("https://"):
            return ToolError(
                f"Unsupported URL scheme. Only http:// and https:// are allowed, got: {url[:80]}",
                title=f"Fetch {url[:60]}",
            )

        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
                resp = await client.get(url, headers={
                    "User-Agent": "Mozilla/5.0 (compatible; MyCode/1.0)",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                })
                resp.raise_for_status()

            content_type = resp.headers.get("content-type", "")

            # Format based on content type
            if "application/json" in content_type:
                import json as _json
                try:
                    parsed = _json.loads(resp.text)
                    text = _json.dumps(parsed, indent=2, ensure_ascii=False)
                except (ValueError, TypeError):
                    text = resp.text
            elif "text/html" in content_type:
                text = _html_to_markdown(resp.text)
            elif "application/xml" in content_type or "text/xml" in content_type:
                text = resp.text  # XML as-is (already structured)
            else:
                text = resp.text

            builder = ToolResultBuilder(max_chars=50_000)
            builder.add(text or "(empty page)")

            return ToolOk(
                builder.build(),
                title=f"Fetch {url[:60]}",
                metadata={"url": url, "status": resp.status_code, "content_type": content_type, "length": len(text)},
            )
        except httpx.HTTPStatusError as e:
            return ToolError(f"HTTP {e.response.status_code}: {e}", title=f"Fetch {url[:60]}")
        except Exception as e:
            return ToolError(f"Error: {e}", title=f"Fetch {url[:60]}")


tool = WebFetchTool()
