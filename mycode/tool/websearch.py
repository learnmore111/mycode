"""WebSearch tool — search the web.

Uses multiple public search endpoints (no API key needed) with graceful
fallbacks for resilience against outages and HTML structure changes.
"""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import unquote

import httpx
from pydantic import BaseModel, Field

from mycode.tool.base import CallableTool, ToolContext, ToolError, ToolOk, ToolResult


class WebSearchParams(BaseModel):
    """Parameters for the websearch tool."""
    query: str = Field(description="Search query")
    max_results: int = Field(default=5, description="Max results to return (default: 5)")


class WebSearchTool(CallableTool[WebSearchParams]):
    id = "websearch"
    description = "Search the web for information. Returns search results with titles, URLs, and snippets."

    def is_read_only(self, args: dict[str, Any] | None = None) -> bool:
        return True

    def is_concurrency_safe(self, args: dict[str, Any] | None = None) -> bool:
        return True

    async def call(self, params: WebSearchParams, ctx: ToolContext) -> ToolResult:
        query = params.query
        max_results = params.max_results

        if not query.strip():
            return ToolError("Search query cannot be empty.", title="Search")

        errors: list[str] = []
        empty_backends: list[str] = []

        try:
            timeout = httpx.Timeout(12.0, connect=5.0)
            async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
                for backend_name, backend in _SEARCH_BACKENDS:
                    try:
                        results = await backend(client, query, max_results)
                    except Exception as e:
                        errors.append(_format_backend_error(backend_name, e))
                        continue

                    if results:
                        output = "\n".join(results)
                        metadata: dict[str, Any] = {
                            "query": query,
                            "results": len(results),
                            "backend": backend_name,
                        }
                        if errors:
                            metadata["fallback_errors"] = errors
                        return ToolOk(output, title=f"Search: {query[:50]}", metadata=metadata)

                    empty_backends.append(backend_name)

            if empty_backends and not errors:
                return ToolOk(
                    "No results found.",
                    title=f"Search: {query[:50]}",
                    metadata={"query": query, "results": 0, "backends": empty_backends},
                )

            details = "; ".join(errors) if errors else "All search backends returned no results."
            return ToolError(
                f"Search failed. {details}",
                title=f"Search: {query[:50]}",
                metadata={"query": query, "errors": errors, "empty_backends": empty_backends},
            )
        except Exception as e:
            detail = str(e).strip() or repr(e)
            return ToolError(f"Search error: {type(e).__name__}: {detail}", title=f"Search: {query[:50]}")


SearchBackend = Callable[[httpx.AsyncClient, str, int], Awaitable[list[str]]]


async def _search_bing_rss(client: httpx.AsyncClient, query: str, max_results: int) -> list[str]:
    resp = await client.get(
        "https://www.bing.com/search",
        params={"q": query, "format": "rss"},
        headers={"User-Agent": "Mozilla/5.0 (compatible; MyCode/1.0)"},
    )
    resp.raise_for_status()
    return _parse_bing_rss_results(resp.text, max_results)


async def _search_ddg_html(client: httpx.AsyncClient, query: str, max_results: int) -> list[str]:
    resp = await client.get(
        "https://html.duckduckgo.com/html/",
        params={"q": query},
        headers={"User-Agent": "Mozilla/5.0 (compatible; MyCode/1.0)"},
    )
    resp.raise_for_status()
    return _parse_ddg_results(resp.text, max_results)


async def _search_bing_html(client: httpx.AsyncClient, query: str, max_results: int) -> list[str]:
    resp = await client.get(
        "https://www.bing.com/search",
        params={"q": query},
        headers={"User-Agent": "Mozilla/5.0 (compatible; MyCode/1.0)"},
    )
    resp.raise_for_status()
    return _parse_bing_html_results(resp.text, max_results)


_SEARCH_BACKENDS: tuple[tuple[str, SearchBackend], ...] = (
    ("bing_rss", _search_bing_rss),
    ("duckduckgo_html", _search_ddg_html),
    ("bing_html", _search_bing_html),
)


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


def _format_backend_error(backend_name: str, error: Exception) -> str:
    detail = str(error).strip() or repr(error)
    return f"{backend_name}: {type(error).__name__}: {detail}"


def _parse_bing_rss_results(body: str, max_results: int) -> list[str]:
    """Parse Bing RSS results."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return []

    results: list[str] = []
    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        snippet = _strip_tags(item.findtext("description") or "")
        if title and url:
            if snippet:
                results.append(f"**{title}**\n{url}\n{snippet}\n")
            else:
                results.append(f"**{title}**\n{url}\n")
        if len(results) >= max_results:
            break
    return results


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


def _parse_bing_html_results(body: str, max_results: int) -> list[str]:
    """Parse Bing HTML results using common result containers."""
    results: list[str] = []
    blocks = re.findall(
        r'<li[^>]+class="[^"]*b_algo[^"]*"[^>]*>.*?<h2[^>]*><a[^>]+href="([^"]+)"[^>]*>(.*?)</a></h2>'
        r'.*?(?:<p[^>]*>(.*?)</p>)?',
        body,
        re.DOTALL,
    )
    for url, title_html, snippet_html in blocks[:max_results]:
        title = _strip_tags(title_html)
        snippet = _strip_tags(snippet_html or "")
        if title and url:
            if snippet:
                results.append(f"**{title}**\n{url}\n{snippet}\n")
            else:
                results.append(f"**{title}**\n{url}\n")
    return results


tool = WebSearchTool()
