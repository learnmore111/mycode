"""WebSearch tool — search the web.

Uses DuckDuckGo HTML (no API key needed) with multiple parsing strategies
for resilience against HTML structure changes.
"""
from __future__ import annotations

import html
import re
from urllib.parse import unquote

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
                    headers={"User-Agent": "Mozilla/5.0 (compatible; MyCode/1.0)"},
                )
                resp.raise_for_status()

            results = _parse_ddg_results(resp.text, max_results)

            if not results:
                return ToolOk(
                    "No results found.",
                    title=f"Search: {query[:50]}",
                    metadata={"query": query, "results": 0},
                )

            output = "\n".join(results)
            return ToolOk(
                output,
                title=f"Search: {query[:50]}",
                metadata={"query": query, "results": len(results)},
            )
        except Exception as e:
            return ToolError(f"Search error: {e}", title=f"Search: {query[:50]}")


def _strip_tags(text: str) -> str:
    """Remove HTML tags and decode entities."""
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def _extract_url(raw_url: str) -> str:
    """Extract actual URL from DuckDuckGo redirect URLs."""
    # DDG wraps URLs like //duckduckgo.com/l/?uddg=https%3A%2F%2F...&rut=...
    match = re.search(r"uddg=([^&]+)", raw_url)
    if match:
        return unquote(match.group(1))
    if raw_url.startswith("http"):
        return raw_url
    return raw_url


def _parse_ddg_results(body: str, max_results: int) -> list[str]:
    """Parse DuckDuckGo HTML with multiple strategies for resilience."""
    results: list[str] = []

    # Strategy 1: Parse result__a + result__snippet pairs
    blocks = re.findall(
        r'<a[^>]+class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?'
        r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
        body, re.DOTALL,
    )
    for raw_url, title_html, snippet_html in blocks[:max_results]:
        title = _strip_tags(title_html)
        snippet = _strip_tags(snippet_html)
        url = _extract_url(raw_url)
        if title and url:
            results.append(f"**{title}**\n{url}\n{snippet}\n")

    if results:
        return results

    # Strategy 2: Parse result__a links only (no snippet)
    links = re.findall(
        r'<a[^>]+class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
        body, re.DOTALL,
    )
    for raw_url, title_html in links[:max_results]:
        title = _strip_tags(title_html)
        url = _extract_url(raw_url)
        if title and url:
            results.append(f"**{title}**\n{url}\n")

    if results:
        return results

    # Strategy 3: Broad fallback — any non-DDG links
    all_links = re.findall(r'href="(https?://[^"]+)"[^>]*>([^<]+)</a>', body)
    for url, title in all_links[:max_results]:
        if "duckduckgo" not in url:
            results.append(f"**{title.strip()}**\n{url}\n")

    return results


tool = WebSearchTool()
