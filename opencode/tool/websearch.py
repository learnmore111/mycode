"""WebSearch tool — search the web. Equivalent to src/tool/websearch.ts.

Uses a simple approach: fetches search results via DuckDuckGo HTML (no API key needed).
"""
from __future__ import annotations

import re
from typing import Any

import httpx

from opencode.tool.base import ToolContext, ToolInfo, ToolResult


class WebSearchTool(ToolInfo):
    id = "websearch"
    description = "Search the web for information. Returns search results with titles, URLs, and snippets."

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "max_results": {"type": "integer", "description": "Max results to return (default: 5)"},
            },
            "required": ["query"],
        }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        query = args["query"]
        max_results = args.get("max_results", 5)

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
            # Find result blocks
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
            return ToolResult(
                title=f"Search: {query[:50]}",
                output=output,
                metadata={"query": query, "results": len(results)},
            )
        except Exception as e:
            return ToolResult(title=f"Search: {query[:50]}", output=f"Search error: {e}", metadata={})


tool = WebSearchTool()
