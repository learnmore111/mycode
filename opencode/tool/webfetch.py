"""WebFetch tool — fetch and extract content from URLs. Equivalent to src/tool/webfetch.ts."""
from __future__ import annotations

import re

import httpx
from pydantic import BaseModel, Field

from opencode.tool.base import CallableTool, ToolContext, ToolError, ToolOk, ToolResult, ToolResultBuilder


def _html_to_text(html: str) -> str:
    """Simple HTML to text conversion (strip tags, decode entities)."""
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&#\d+;", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class WebFetchParams(BaseModel):
    """Parameters for the webfetch tool."""
    url: str = Field(description="The URL to fetch content from")
    extract: str | None = Field(default=None, description="What information to extract from the page")


class WebFetchTool(CallableTool[WebFetchParams]):
    id = "webfetch"
    description = "Fetch content from a URL. Returns the page content as text. HTTP URLs are upgraded to HTTPS."

    async def call(self, params: WebFetchParams, ctx: ToolContext) -> ToolResult:
        url = params.url
        if url.startswith("http://"):
            url = "https://" + url[7:]

        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
                resp = await client.get(url, headers={"User-Agent": "OpenCode/1.0"})
                resp.raise_for_status()

            content_type = resp.headers.get("content-type", "")
            text = _html_to_text(resp.text) if "text/html" in content_type else resp.text

            # Use ToolResultBuilder for truncation
            builder = ToolResultBuilder(max_chars=50_000)
            builder.add(text or "(empty page)")

            return ToolOk(
                builder.build(),
                title=f"Fetch {url[:60]}",
                metadata={"url": url, "status": resp.status_code, "length": len(text)},
            )
        except httpx.HTTPStatusError as e:
            return ToolError(f"HTTP {e.response.status_code}: {e}", title=f"Fetch {url[:60]}")
        except Exception as e:
            return ToolError(f"Error: {e}", title=f"Fetch {url[:60]}")


tool = WebFetchTool()
