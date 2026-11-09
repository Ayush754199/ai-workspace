# Backend-only Tavily Search and Extract client with account-aware failover.

from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import requests
from app.core.config import settings
from app.core.netguard import UnsafeURLError, assert_safe_url
TAVILY_BASE_URL = "https://api.tavily.com"

class TavilyError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable
@dataclass(frozen=True)
class TavilyResult:
    data: dict[str, Any]
    account: str

def _credentials() -> list[tuple[str, str]]:
    return [
        ("account_1", settings.TAVILY_API_KEY_1.strip()),
        ("account_2", settings.TAVILY_API_KEY_2.strip()),
    ]


def request(endpoint: str, payload: dict[str, Any]) -> TavilyResult:
    # Use account 2 only for eligible 5xx failover; never rotate across
    # authentication, quota/rate-limit, or uncertain transport errors.
    credentials = [(label, key) for label, key in _credentials() if key]
    if not credentials:
        raise TavilyError("Tavily credentials are not configured")

    url = f"{TAVILY_BASE_URL}/{endpoint.lstrip('/')}"
    for index, (account, key) in enumerate(credentials):
        try:
            response = requests.post(
                url,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=payload,
                timeout=settings.TAVILY_REQUEST_TIMEOUT_SECONDS,
            )
        except (requests.Timeout, requests.ConnectionError) as exc:
            # The server may have accepted the request before the connection failed;
            # do not issue a duplicate request using the second account.
            raise TavilyError("Tavily request outcome is uncertain; retry manually", retryable=False) from exc
        except requests.RequestException as exc:
            raise TavilyError("Tavily request failed", retryable=False) from exc
        if response.status_code in {500, 502, 503, 504} and index == 0 and len(credentials) > 1 and settings.TAVILY_TECHNICAL_FAILOVER_ENABLED:
            continue
        if response.status_code in {401, 403, 429}:
            raise TavilyError(
                f"Tavily rejected the request (HTTP {response.status_code})",
                status_code=response.status_code,
                retryable=False,
            )
        if not response.ok:
            # Do not expose response text: provider errors can contain operational data.
            raise TavilyError(
                f"Tavily rejected the request (HTTP {response.status_code})",
                status_code=response.status_code,
                retryable=response.status_code in {500, 502, 503, 504},
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise TavilyError("Tavily returned an invalid response") from exc
        if not isinstance(data, dict):
            raise TavilyError("Tavily returned an unexpected response")
        return TavilyResult(data=data, account=account)

    raise TavilyError("Tavily is temporarily unavailable", retryable=True)


def search(query: str, *, limit: int = 6, topic: str = "general") -> tuple[list[dict[str, Any]], dict[str, Any]]:
    clean_query = (query or "").strip()
    if not clean_query:
        return [], {"credits": 0, "account": None, "request_id": None}
    if topic not in {"general", "news", "finance"}:
        topic = "general"
    result = request(
        "search",
        {
            "query": clean_query,
            "topic": topic,
            "search_depth": "basic",
            "max_results": max(1, min(limit, 20)),
            "include_answer": False,
            "include_usage": True,
            "include_published_date": True,
        },
    )
    rows: list[dict[str, Any]] = []
    for item in result.data.get("results", []):
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        try:
            assert_safe_url(url)
        except (UnsafeURLError, ValueError):
            continue
        rows.append(
            {
                "title": str(item.get("title") or url)[:300],
                "url": url,
                "snippet": str(item.get("content") or "")[:1800],
                "published_date": item.get("published_date"),
            }
        )
    usage = result.data.get("usage") if isinstance(result.data.get("usage"), dict) else {}
    return rows, {
        "credits": int(usage.get("credits") or 0),
        "account": result.account,
        "request_id": result.data.get("request_id"),
    }


def extract(urls: list[str], *, query: str | None = None) -> tuple[dict[str, str], dict[str, Any]]:
    safe_urls: list[str] = []
    for url in urls[:20]:
        try:
            safe_urls.append(assert_safe_url(url))
        except (UnsafeURLError, ValueError):
            continue
    if not safe_urls:
        return {}, {"credits": 0, "account": None, "request_id": None, "successful_urls": 0}

    payload: dict[str, Any] = {
        "urls": safe_urls,
        "extract_depth": "basic",
        "format": "markdown",
        "include_usage": True,
        "timeout": settings.TAVILY_REQUEST_TIMEOUT_SECONDS,
    }
    if query:
        payload["query"] = query[:500]
        payload["chunks_per_source"] = 3
    result = request("extract", payload)
    contents: dict[str, str] = {}
    for item in result.data.get("results", []):
        if isinstance(item, dict) and item.get("url") and item.get("raw_content"):
            contents[str(item["url"])] = str(item["raw_content"])[:12000]
    usage = result.data.get("usage") if isinstance(result.data.get("usage"), dict) else {}
    return contents, {
        "credits": int(usage.get("credits") or 0),
        "account": result.account,
        "request_id": result.data.get("request_id"),
        "successful_urls": len(contents),
    }
