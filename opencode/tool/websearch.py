"""WebSearch tool — search the web. Equivalent to src/tool/websearch.ts.

Uses a simple approach: fetches search results via DuckDuckGo HTML (no API key needed).
"""
from __future__ import annotations

import re

import httpx
from pydantic import BaseModel, Field

from opencode.tool.base import CallableTool, ToolContext, ToolError, ToolOk, ToolResult


class WebSearchParams(BaseModel):
    """Parameters for the websearch tool."""
    query: str = Field(description="Search query")
    max_results: int = Field(default=5, description="Max results to return (default: 5)")


class WebSearchTool(CallableTool[WebSearchParams]):
    id = "websearch"
    description = "Search the web for information. Returns search results with titles, URLs, and snippets."

    async def call(self, params: WebSearchParams, ctx: ToolContext) -> ToolResult:
        query = params.query
        max_results = params.max_results

        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
                resp = await client.get(
                    "https://html.duckduckgo.com/html/",
                    params={"q": query},
                    headers={"User-Agent": "OpenCode/1.0"},
                )
                resp.raise_for_status()

            # Parse DuckDuckGo HTML results
            results: list[str] = []
            blocks = re.findall(
                r'<a[^>]+class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?'
                r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
                resp.text, re.DOTALL,
            )
            for url, title, snippet in blocks[:max_results]:
                title_clean = re.sub(r"<[^>]+>", "", title).strip()
                snippet_clean = re.sub(r"<[^>]+>", "", snippet).strip()
                if title_clean and url:
                    results.append(f"**{title_clean}**\n{url}\n{snippet_clean}\n")

            if not results:
                # Fallback: try to extract any links
                links = re.findall(r'href="(https?://[^"]+)"[^>]*>([^<]+)</a>', resp.text)
                for url, title in links[:max_results]:
                    if "duckduckgo" not in url:
                        results.append(f"**{title.strip()}**\n{url}\n")

            output = "\n".join(results) if results else "No results found."
            return ToolOk(
                output,
                title=f"Search: {query[:50]}",
                metadata={"query": query, "results": len(results)},
            )
        except Exception as e:
            return ToolError(f"Search error: {e}", title=f"Search: {query[:50]}")


tool = WebSearchTool()
