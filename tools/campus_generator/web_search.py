"""Bounded, provider-neutral web search discovery.

Search providers only discover candidate page URLs.  Fetching and validating
the pages themselves remains the responsibility of :class:`PublicWebHarvester`.
Keeping those concerns separate ensures that every discovered URL still passes
the existing HTTPS, DNS/public-address, redirect, content-type and size checks.
"""

from __future__ import annotations

import ipaddress
import math
import os
from dataclasses import asdict, dataclass
from typing import Callable
from urllib.parse import SplitResult, urlsplit, urlunsplit

import requests

from .harvest import HarvestCache


BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
TAVILY_ENDPOINT = "https://api.tavily.com/search"

_PROVIDER_ENV_VARS = {
    "brave": "BRAVE_SEARCH_API_KEY",
    "tavily": "TAVILY_API_KEY",
}


@dataclass(frozen=True)
class SearchHit:
    """A sanitized web result with enough provenance for review."""

    url: str
    title: str
    snippet: str
    provider: str
    query: str
    rank: int
    score: float | None = None

    def to_dict(self) -> dict:
        """Return a JSON-serializable representation."""

        return asdict(self)


@dataclass(frozen=True)
class SearchResult:
    """Aggregated results for one bounded multi-query search."""

    hits: list[SearchHit]
    provider: str
    queries: list[str]
    warnings: list[str]
    cached_queries: int = 0

    @property
    def urls(self) -> list[str]:
        return [hit.url for hit in self.hits]

    def to_dict(self) -> dict:
        """Return a JSON-serializable representation."""

        return {
            "hits": [hit.to_dict() for hit in self.hits],
            "provider": self.provider,
            "queries": list(self.queries),
            "warnings": list(self.warnings),
            "cachedQueries": self.cached_queries,
        }


def _clean_text(value, maximum: int) -> str:
    return " ".join(str(value or "").split())[:maximum]


def normalize_search_url(value: str) -> str:
    """Canonicalize a search-result URL using network-free safety checks.

    Only HTTPS URLs without credentials and without a non-standard port are
    admitted.  Fragments are discarded, an explicit ``:443`` is normalized
    away and internationalized host names are converted to IDNA.  Public-IP
    validation intentionally happens later in ``PublicWebHarvester`` so search
    aggregation never performs DNS lookups.
    """

    raw = str(value or "").strip()
    if not raw or len(raw) > 4096 or any(ord(char) < 32 for char in raw):
        raise ValueError("search result URL is empty or malformed")
    parsed = urlsplit(raw)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("search result URL must use HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("search result URL must not contain credentials")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("search result URL has an invalid port") from exc
    if port not in (None, 443):
        raise ValueError("search result URL must use the standard HTTPS port")
    try:
        hostname = parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()
    except (UnicodeError, ValueError) as exc:
        raise ValueError("search result URL has an invalid host") from exc
    if not hostname:
        raise ValueError("search result URL has an invalid host")
    try:
        literal_address = ipaddress.ip_address(hostname)
    except ValueError:
        literal_address = None
    if literal_address is not None and not literal_address.is_global:
        raise ValueError("search result URL must not use a non-public IP address")

    # ``urlsplit().hostname`` removes IPv6 brackets, which need restoring in
    # a serialized authority.  DNS/public-IP policy is deliberately deferred.
    authority_host = f"[{hostname}]" if ":" in hostname else hostname
    path = parsed.path or "/"
    normalized = SplitResult("https", authority_host, path, parsed.query, "")
    return urlunsplit(normalized)


class WebSearchClient:
    """Search Brave or Tavily and return a small, diverse URL set.

    ``request_json`` is injectable for deterministic/offline callers.  It
    receives the provider-native request mapping: query parameters for Brave,
    and the JSON body for Tavily.
    """

    def __init__(self, provider: str = "brave", api_key: str | None = None,
                 cache: HarvestCache | None = None, *, max_results: int = 10,
                 per_domain_limit: int = 2, max_queries: int = 4,
                 timeout: float = 10, language: str | None = "zh-cn",
                 request_json: Callable[[dict], dict] | None = None):
        provider_name = str(provider or "").strip().lower()
        if provider_name not in _PROVIDER_ENV_VARS:
            supported = ", ".join(sorted(_PROVIDER_ENV_VARS))
            raise ValueError(f"unsupported web search provider; choose one of: {supported}")
        self.provider = provider_name
        self.api_key = (api_key if api_key is not None
                        else os.getenv(_PROVIDER_ENV_VARS[provider_name], "")).strip()
        self.cache = cache
        self.max_results = min(max(int(max_results), 1), 20)
        self.per_domain_limit = min(max(int(per_domain_limit), 1), 10)
        self.max_queries = min(max(int(max_queries), 1), 8)
        self.timeout = min(max(float(timeout), 1), 20)
        self.language = _clean_text(language, 32) or None
        self.request_json = request_json or self._request_json

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @property
    def api_key_env_var(self) -> str:
        return _PROVIDER_ENV_VARS[self.provider]

    def _request_json(self, payload: dict) -> dict:
        if self.provider == "brave":
            response = requests.get(
                BRAVE_ENDPOINT,
                params=payload,
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": self.api_key,
                    "User-Agent": "CampusAssetResearch/1.0",
                },
                timeout=self.timeout,
            )
        else:
            response = requests.post(
                TAVILY_ENDPOINT,
                json=payload,
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "CampusAssetResearch/1.0",
                },
                timeout=self.timeout,
            )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError("search provider returned a non-object response")
        return result

    def _request_payload(self, query: str, count: int) -> dict:
        if self.provider == "brave":
            return {
                "q": query,
                "count": min(count, 20),
                "country": "CN",
                "search_lang": "zh-hans",
                "ui_lang": "zh-CN",
                "safesearch": "moderate",
            }
        payload = {
            "query": query,
            "topic": "general",
            "search_depth": "basic",
            "max_results": min(count, 20),
            "include_answer": False,
            "include_raw_content": False,
            "safe_search": True,
        }
        if self.language:
            # Tavily treats this as a ranking boost unless strict filtering is
            # explicitly requested, so other-language campus sources remain
            # discoverable.
            payload.update({"language": self.language, "filter_by_language": False})
        return payload

    def _provider_rows(self, payload: dict) -> list[dict]:
        if not isinstance(payload, dict):
            raise ValueError("search provider returned a non-object response")
        if payload.get("error"):
            raise RuntimeError("search provider reported an error")
        if self.provider == "brave":
            web = payload.get("web") or {}
            if not isinstance(web, dict):
                raise ValueError("Brave response has an invalid web result")
            rows = web.get("results") or []
        else:
            rows = payload.get("results") or []
        if not isinstance(rows, list):
            raise ValueError("search provider returned an invalid result list")
        return [row for row in rows if isinstance(row, dict)]

    def _parse_hits(self, query: str, payload: dict) -> list[SearchHit]:
        hits = []
        for rank, row in enumerate(self._provider_rows(payload), 1):
            try:
                url = normalize_search_url(row.get("url", ""))
            except ValueError:
                continue
            raw_score = row.get("score")
            score = None
            if raw_score is not None:
                try:
                    parsed_score = float(raw_score)
                    if math.isfinite(parsed_score):
                        score = parsed_score
                except (TypeError, ValueError):
                    pass
            hits.append(SearchHit(
                url=url,
                title=_clean_text(row.get("title"), 300),
                snippet=_clean_text(row.get("description") if self.provider == "brave"
                                    else row.get("content"), 1200),
                provider=self.provider,
                query=query,
                rank=rank,
                score=score,
            ))
        return hits

    @staticmethod
    def _cached_hits(value, provider: str, query: str) -> list[SearchHit]:
        if not isinstance(value, list):
            raise ValueError("cached search result is not a list")
        hits = []
        for row in value:
            if not isinstance(row, dict):
                continue
            try:
                hits.append(SearchHit(
                    url=normalize_search_url(row.get("url", "")),
                    title=_clean_text(row.get("title"), 300),
                    snippet=_clean_text(row.get("snippet"), 1200),
                    provider=provider,
                    query=query,
                    rank=max(1, int(row.get("rank", len(hits) + 1))),
                    score=(float(row["score"]) if row.get("score") is not None else None),
                ))
            except (TypeError, ValueError, OverflowError):
                continue
        return hits

    def _search_one(self, query: str, count: int) -> tuple[list[SearchHit], bool]:
        cache_parameters = {
            "provider": self.provider,
            "query": query,
            "count": count,
            "language": self.language,
            "schemaVersion": 2,
        }
        source = f"web_search_{self.provider}"
        cached = self.cache.get(source, cache_parameters) if self.cache else None
        if cached is not None:
            return self._cached_hits(cached, self.provider, query), True
        payload = self.request_json(self._request_payload(query, count))
        hits = self._parse_hits(query, payload)
        if self.cache:
            self.cache.put(source, cache_parameters, [hit.to_dict() for hit in hits])
        return hits, False

    def search(self, queries: str | list[str] | tuple[str, ...]) -> SearchResult:
        """Search up to ``max_queries`` and fairly merge provider results.

        Individual request failures are reported as warnings so another query
        can still produce useful candidates.  Missing credentials are a hard
        configuration error and are never silently downgraded.
        """

        raw_queries = [queries] if isinstance(queries, str) else list(queries or [])
        cleaned_queries = []
        seen_queries = set()
        for value in raw_queries:
            query = _clean_text(value, 500)
            if query and query not in seen_queries:
                cleaned_queries.append(query)
                seen_queries.add(query)
            if len(cleaned_queries) >= self.max_queries:
                break
        if not cleaned_queries:
            raise ValueError("at least one non-empty web search query is required")
        if not self.configured:
            raise ValueError(f"{self.api_key_env_var} is not configured")

        per_query_count = min(20, max(1, math.ceil(self.max_results / len(cleaned_queries))))
        warnings: list[str] = []
        query_hits: list[list[SearchHit]] = []
        cached_queries = 0
        for query in cleaned_queries:
            try:
                hits, cached = self._search_one(query, per_query_count)
                query_hits.append(hits)
                cached_queries += int(cached)
            except (requests.RequestException, RuntimeError, ValueError, TypeError) as exc:
                message = str(exc)
                if self.api_key:
                    message = message.replace(self.api_key, "[redacted]")
                warnings.append(
                    f"{self.provider} search query {query!r}: {type(exc).__name__}: {message}"
                )
                query_hits.append([])

        # Round-robin merging prevents the first query from consuming the
        # complete result budget.  A per-host ceiling also improves diversity.
        selected: list[SearchHit] = []
        seen_urls: set[str] = set()
        domain_counts: dict[str, int] = {}
        max_depth = max((len(hits) for hits in query_hits), default=0)
        for index in range(max_depth):
            for hits in query_hits:
                if index >= len(hits):
                    continue
                hit = hits[index]
                host = (urlsplit(hit.url).hostname or "").lower()
                if hit.url in seen_urls or domain_counts.get(host, 0) >= self.per_domain_limit:
                    continue
                selected.append(hit)
                seen_urls.add(hit.url)
                domain_counts[host] = domain_counts.get(host, 0) + 1
                if len(selected) >= self.max_results:
                    break
            if len(selected) >= self.max_results:
                break
        return SearchResult(selected, self.provider, cleaned_queries, warnings, cached_queries)


__all__ = [
    "BRAVE_ENDPOINT",
    "TAVILY_ENDPOINT",
    "SearchHit",
    "SearchResult",
    "WebSearchClient",
    "normalize_search_url",
]
