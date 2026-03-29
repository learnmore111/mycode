"""WebFetch tool — fetch and extract content from URLs. Equivalent to src/tool/webfetch.ts."""
from __future__ import annotations

import re
from typing import Any

import httpx

from opencode.tool.base import ToolContext, ToolInfo, ToolResult


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


class WebFetchTool(ToolInfo):
    id = "webfetch"
    description = "Fetch content from a URL. Returns the page content as text. HTTP URLs are upgraded to HTTPS."

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to fetch content from"},
                "extract": {"type": "string", "description": "What information to extract from the page"},
            },
            "required": ["url"],
        }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        url = args["url"]
        if url.startswith("http://"):
            url = "https://" + url[7:]

        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
                resp = await client.get(url, headers={"User-Agent": "OpenCode/1.0"})
                resp.raise_for_status()

            content_type = resp.headers.get("content-type", "")
            text = _html_to_text(resp.text) if "text/html" in content_type else resp.text

            # Truncate very long pages
            if len(text) > 50_000:
                text = text[:50_000] + f"\n\n... truncated ({len(text)} chars total)"

            return ToolResult(
                title=f"Fetch {url[:60]}",
                output=text or "(empty page)",
                metadata={"url": url, "status": resp.status_code, "length": len(text)},
            )
        except httpx.HTTPStatusError as e:
            return ToolResult(title=f"Fetch {url[:60]}", output=f"HTTP {e.response.status_code}: {e}", metadata={})
        except Exception as e:
            return ToolResult(title=f"Fetch {url[:60]}", output=f"Error: {e}", metadata={})


tool = WebFetchTool()
