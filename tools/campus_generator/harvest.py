"""Candidate harvesting adapters for map POIs and public web evidence."""

from __future__ import annotations

import json
import hashlib
import ipaddress
import math
import os
import re
import socket
import threading
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Protocol
from urllib.parse import urlparse

import requests

from .boundary import CampusMembershipEvaluator, membership_evidence


@dataclass(frozen=True)
class HarvestResult:
    candidates: list[dict]
    source: str
    warnings: list[str]


class Harvester(Protocol):
    source: str

    def collect(self) -> HarvestResult:
        """Collect raw candidates without writing runtime assets."""


class JsonHarvester:
    """Read a reviewed/exported candidate list from a JSON file."""

    source = "json"

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def collect(self) -> HarvestResult:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("harvest input must be a JSON array")
        candidates = []
        warnings = []
        for index, item in enumerate(data):
            if not isinstance(item, dict):
                warnings.append(f"candidate[{index}] is not an object")
                continue
            candidate = dict(item)
            candidate.setdefault("source", self.source)
            candidates.append(candidate)
        return HarvestResult(candidates, self.source, warnings)


class HarvestCache:
    """Small atomic JSON cache for expensive or rate-limited source requests."""

    def __init__(self, directory: str | Path, ttl_seconds: int = 86400):
        self.directory = Path(directory)
        self.ttl_seconds = max(0, int(ttl_seconds))

    def key(self, source: str, parameters: dict) -> str:
        payload = json.dumps(parameters, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(f"{source}\n{payload}".encode("utf-8")).hexdigest()

    def get(self, source: str, parameters: dict):
        path = self.directory / f"{self.key(source, parameters)}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            entry_ttl = max(0, int(data.get("ttlSeconds", self.ttl_seconds)))
            effective_ttl = min(self.ttl_seconds, entry_ttl)
            if time.time() - float(data["storedAt"]) > effective_ttl:
                return None
            return data["value"]
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def put(self, source: str, parameters: dict, value, *,
            ttl_seconds: int | None = None) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{self.key(source, parameters)}.json"
        temporary = path.with_suffix(".tmp")
        record = {"storedAt": time.time(), "value": value}
        if ttl_seconds is not None:
            record["ttlSeconds"] = max(0, int(ttl_seconds))
        temporary.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)


class AmapProviderError(RuntimeError):
    """A provider-declared failure without request URL or credential details."""

    def __init__(self, info: object = None, infocode: object = None):
        super().__init__("AMap provider request failed")
        self.info = str(info or "unknown error")
        self.infocode = str(infocode or "unknown")


_AMAP_RETRYABLE_INFOCODES = {
    # Provider-side QPS, gateway, busy and temporarily unavailable failures.
    "10014", "10015", "10016", "10017", "10019", "10020", "10021", "10022", "10023",
}
_AMAP_RETRYABLE_INFO_MARKERS = (
    "QPS", "QPM", "BUSY", "GATEWAY", "TIMEOUT", "TEMPORAR", "UNAVAILABLE", "繁忙", "超限",
)
_SAFE_LOG_URL = re.compile(r"https?://\S+", re.IGNORECASE)
_SAFE_LOG_CREDENTIAL = re.compile(
    r"(?i)\b(?:key|api[_-]?key|token|authorization)\s*[=:]\s*[^\s,;]+"
)


def _bounded_env_number(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    if not math.isfinite(value):
        value = default
    return min(max(value, minimum), maximum)


def _safe_log_value(value: object, api_key: str = "") -> str:
    """Keep provider diagnostics useful while stripping URLs and credentials."""

    text = " ".join(str(value or "unknown").split())
    if api_key:
        text = text.replace(api_key, "[redacted-key]")
    text = _SAFE_LOG_URL.sub("[redacted-url]", text)
    text = _SAFE_LOG_CREDENTIAL.sub("[redacted-credential]", text)
    return text[:240]


class _AmapRequestGate:
    """Process-wide request-start limiter shared by production harvesters."""

    def __init__(self):
        self._lock = threading.Lock()
        self._last_started: float | None = None

    def wait(self, interval: float) -> None:
        if interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            if self._last_started is not None:
                delay = interval - (now - self._last_started)
                if delay > 0:
                    time.sleep(delay)
            self._last_started = time.monotonic()


_AMAP_REQUEST_GATE = _AmapRequestGate()


class _AmapRequestFailure(RuntimeError):
    """Internal wrapper carrying only already-sanitized request diagnostics."""

    def __init__(self, cause: Exception, failures: list[str]):
        super().__init__("AMap request attempts failed")
        self.cause = cause
        self.failures = list(failures)


class AmapPoiHarvester:
    """Collect nearby AMap POIs around a configured campus center.

    AMap coordinates are GCJ-02. Candidates are limited to the profile radius
    and retain provider IDs, type, address, distance and source evidence.
    """

    source = "amap"
    endpoint = "https://restapi.amap.com/v3/place/around"

    def __init__(self, profile: dict, keywords: list[str], api_key: str | None = None,
                 cache: HarvestCache | None = None, *, max_pages: int = 2,
                 page_size: int = 25, timeout: float = 10,
                 request_json: Callable | None = None,
                 request_interval_seconds: float | None = None,
                 max_retries: int | None = None,
                 retry_backoff_seconds: float | None = None,
                 empty_cache_ttl_seconds: int | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 monotonic: Callable[[], float] = time.monotonic):
        self.profile = profile
        self.keywords = list(dict.fromkeys(str(k).strip() for k in keywords if str(k).strip()))[:20]
        self.api_key = (api_key or os.getenv("AMAP_WEB_SERVICE_KEY", "")).strip()
        self.cache = cache
        self.max_pages = min(max(int(max_pages), 1), 5)
        self.page_size = min(max(int(page_size), 1), 25)
        self.timeout = min(max(float(timeout), 1), 20)
        default_request = self._request_json
        self.request_json = request_json or default_request
        self._default_request_json = default_request if request_json is None else None
        self._custom_request = request_json is not None
        self._request_interval_explicit = request_interval_seconds is not None
        default_interval = 0.0 if self._custom_request else _bounded_env_number(
            "AMAP_REQUEST_INTERVAL_SECONDS", 0.25, 0.0, 10.0,
        )
        self.request_interval_seconds = min(max(float(
            default_interval if request_interval_seconds is None else request_interval_seconds
        ), 0.0), 10.0)
        configured_retries = _bounded_env_number("AMAP_MAX_RETRIES", 2, 0, 5)
        self.max_retries = min(max(int(
            configured_retries if max_retries is None else max_retries
        ), 0), 5)
        configured_backoff = _bounded_env_number("AMAP_RETRY_BACKOFF_SECONDS", 0.5, 0.0, 10.0)
        self.retry_backoff_seconds = min(max(float(
            configured_backoff if retry_backoff_seconds is None else retry_backoff_seconds
        ), 0.0), 10.0)
        configured_empty_ttl = _bounded_env_number("AMAP_EMPTY_CACHE_TTL_SECONDS", 300, 0, 3600)
        self.empty_cache_ttl_seconds = min(max(int(
            configured_empty_ttl if empty_cache_ttl_seconds is None else empty_cache_ttl_seconds
        ), 0), 3600)
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_request_started: float | None = None

    def _request_json(self, params: dict) -> dict:
        response = requests.get(self.endpoint, params=params, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def _wait_for_request_slot(self) -> None:
        if self.request_interval_seconds <= 0:
            return
        # Tests and offline callers sometimes replace request_json after
        # construction.  Such injected requesters remain unthrottled unless
        # an interval was explicitly requested.
        using_default_request = (
            self._default_request_json is not None and
            self.request_json is self._default_request_json
        )
        if not using_default_request and not self._request_interval_explicit:
            return
        if using_default_request and self._sleep is time.sleep and self._monotonic is time.monotonic:
            _AMAP_REQUEST_GATE.wait(self.request_interval_seconds)
            return
        now = self._monotonic()
        if self._last_request_started is not None:
            delay = self.request_interval_seconds - (now - self._last_request_started)
            if delay > 0:
                self._sleep(delay)
        self._last_request_started = self._monotonic()

    @staticmethod
    def _validated_payload(payload: object) -> dict:
        if not isinstance(payload, dict):
            raise ValueError("AMap response must be a JSON object")
        if str(payload.get("status", "")) != "1":
            raise AmapProviderError(payload.get("info"), payload.get("infocode"))
        if payload.get("pois") is not None and not isinstance(payload.get("pois"), list):
            raise ValueError("AMap response field 'pois' must be a list")
        return payload

    @staticmethod
    def _retryable_error(exc: Exception) -> bool:
        if isinstance(exc, AmapProviderError):
            info = exc.info.upper()
            return (exc.infocode in _AMAP_RETRYABLE_INFOCODES or
                    any(marker in info for marker in _AMAP_RETRYABLE_INFO_MARKERS))
        if isinstance(exc, requests.HTTPError):
            status = getattr(getattr(exc, "response", None), "status_code", None)
            return status in {429, 500, 502, 503, 504}
        return isinstance(exc, (requests.Timeout, requests.ConnectionError))

    def _safe_error_detail(self, exc: Exception) -> str:
        if isinstance(exc, AmapProviderError):
            info = _safe_log_value(exc.info, self.api_key)
            infocode = _safe_log_value(exc.infocode, self.api_key)
            return f"provider error info={info} infocode={infocode}"
        if isinstance(exc, requests.HTTPError):
            status = getattr(getattr(exc, "response", None), "status_code", None)
            return f"HTTPError httpStatus={status if status is not None else 'unknown'}"
        return type(exc).__name__

    def _request_page(self, params: dict) -> tuple[dict, list[str]]:
        failures: list[str] = []
        for attempt in range(self.max_retries + 1):
            self._wait_for_request_slot()
            try:
                return self._validated_payload(self.request_json(params)), failures
            except (requests.RequestException, RuntimeError, ValueError) as exc:
                failures.append(self._safe_error_detail(exc))
                if attempt >= self.max_retries or not self._retryable_error(exc):
                    raise _AmapRequestFailure(exc, failures) from exc
                delay = self.retry_backoff_seconds * (2 ** attempt)
                if delay > 0:
                    self._sleep(delay)
        raise AssertionError("unreachable")

    def _collect_keyword(self, keyword: str) -> tuple[list[dict], list[str]]:
        campus = self.profile["campus"]
        if campus.get("coordinateSystem") not in (None, "GCJ-02"):
            raise ValueError("AMap harvesting requires a GCJ-02 campus center")
        center = [float(value) for value in campus["center"]]
        membership_evaluator = CampusMembershipEvaluator(campus)
        required_radius = membership_evaluator.covering_radius_m()
        if required_radius > 50000:
            raise ValueError("campus boundary exceeds AMap's 50000 metre around-search limit")
        radius = min(max(int(math.ceil(required_radius)), 100), 50000)
        boundary = campus.get("boundary")
        params_for_cache = {"campus": campus["slug"], "center": center, "radius": radius,
                            "keyword": keyword, "pages": self.max_pages,
                            "pageSize": self.page_size,
                            # Invalidates legacy entries that may contain failed partial/empty results.
                            "requestPolicyVersion": 2,
                            "spatialPolicy": {
                                "version": 2,
                                "method": "polygon" if boundary is not None else "radius_fallback",
                                "trustRadiusM": float(campus["trustRadiusM"]),
                                "coordinateSystem": campus.get("coordinateSystem"),
                                "boundaryHash": membership_evaluator.boundary_digest,
                                "boundarySource": campus.get("boundarySource"),
                                "boundaryConfidence": campus.get("boundaryConfidence"),
                            }}
        cached = self.cache.get(self.source, params_for_cache) if self.cache else None
        if cached is not None:
            warnings = []
            if cached == []:
                warnings.append(
                    f"AMap keyword {_safe_log_value(keyword, self.api_key)!r}: "
                    "using cached successful empty result"
                )
            return cached, warnings
        if not self.api_key:
            raise ValueError("AMAP_WEB_SERVICE_KEY is not configured")
        results, warnings = [], []
        completed = True
        excluded_outside = 0
        for page in range(1, self.max_pages + 1):
            params = {
                "key": self.api_key,
                "location": f"{center[0]},{center[1]}",
                "keywords": keyword,
                "radius": radius,
                "offset": self.page_size,
                "page": page,
                "extensions": "all",
            }
            try:
                payload, retry_failures = self._request_page(params)
                if retry_failures:
                    warnings.append(
                        f"AMap keyword {_safe_log_value(keyword, self.api_key)!r} page {page}: "
                        "recovered after "
                        f"{len(retry_failures) + 1} attempts; prior failures: "
                        + "; ".join(retry_failures)
                    )
            except _AmapRequestFailure as exc:
                completed = False
                partial_note = (
                    "; partial result not cached" if page > 1 else "; failed result not cached"
                )
                warnings.append(
                    f"AMap keyword {_safe_log_value(keyword, self.api_key)!r} page {page}: failed after "
                    f"{len(exc.failures)} attempt(s): {'; '.join(exc.failures)}{partial_note}"
                )
                break
            pois = payload.get("pois") or []
            for poi in pois:
                try:
                    lng_text, lat_text = str(poi.get("location", "")).split(",", 1)
                    lng, lat = float(lng_text), float(lat_text)
                    accepted, distance, membership = membership_evaluator.evaluate((lng, lat))
                    if not math.isfinite(distance) or not accepted:
                        excluded_outside += 1
                        continue
                    name = str(poi.get("name") or "").strip()
                    if not name:
                        continue
                    poi_type = str(poi.get("type") or "地点")
                    address = str(poi.get("address") or "")
                    provider_id = str(poi.get("id") or "")
                    results.append({
                        "name": name,
                        "locationName": name,
                        "category": "scene",
                        "subCategory": poi_type.split(";")[0] or "地点",
                        "lng": lng,
                        "lat": lat,
                        "tags": [keyword, *[part for part in poi_type.split(";") if part]],
                        "text": " ".join(part for part in (name, poi_type, address, keyword,
                                                                campus["name"], self.profile["school"]["name"])
                                          if part),
                        "source": self.source,
                        "sourceUrls": [],
                        "sourceId": provider_id,
                        "coordinateSystem": "GCJ-02",
                        "confidence": 0.72,
                        "verified": False,
                        "evidence": {"provider": "Amap", "type": poi_type,
                                     "address": address,
                                     "keyword": keyword, "queryRadiusMeters": radius,
                                     **membership_evidence(membership, distance)},
                        "meta": {"address": address, "amapType": poi_type,
                                 "amapId": provider_id, "sourceQuery": keyword},
                    })
                except (TypeError, ValueError):
                    continue
            if len(pois) < self.page_size:
                break
        if excluded_outside:
            method = "polygon" if boundary is not None else "radius fallback"
            warnings.append(
                f"AMap keyword {_safe_log_value(keyword, self.api_key)!r}: excluded "
                f"{excluded_outside} candidates outside campus {method}"
            )
        if completed:
            if self.cache and results:
                self.cache.put(self.source, params_for_cache, results)
            elif self.cache:
                self.cache.put(
                    self.source, params_for_cache, results,
                    ttl_seconds=self.empty_cache_ttl_seconds,
                )
            if not results:
                cache_note = (
                    f"; cached for up to {self.empty_cache_ttl_seconds} seconds"
                    if self.cache else "; cache disabled"
                )
                warnings.append(
                    f"AMap keyword {_safe_log_value(keyword, self.api_key)!r}: request completed "
                    f"successfully with 0 accepted POIs{cache_note}"
                )
        return results, warnings

    def collect(self) -> HarvestResult:
        all_candidates, warnings = [], []
        for keyword in self.keywords:
            candidates, keyword_warnings = self._collect_keyword(keyword)
            all_candidates.extend(candidates)
            warnings.extend(keyword_warnings)
        return HarvestResult(all_candidates, self.source, warnings)


class _PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_script = False
        self.script_type = ""
        self.json_ld: list[str] = []
        self.text_parts: list[str] = []
        self._script_parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "script":
            attrs = dict(attrs)
            self.in_script = True
            self.script_type = attrs.get("type", "").lower()
            self._script_parts = []

    def handle_endtag(self, tag):
        if tag.lower() == "script" and self.in_script:
            if self.script_type == "application/ld+json":
                self.json_ld.append("".join(self._script_parts))
            self.in_script = False
            self.script_type = ""

    def handle_data(self, data):
        if self.in_script:
            if self.script_type == "application/ld+json":
                self._script_parts.append(data)
            return
        clean = " ".join(data.split())
        if clean:
            self.text_parts.append(clean)


def _host_is_public(host: str) -> bool:
    try:
        addresses = {ipaddress.ip_address(host)}
    except ValueError:
        try:
            addresses = {ipaddress.ip_address(item[4][0])
                         for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)}
        except OSError:
            return False
    return bool(addresses) and all(address.is_global for address in addresses)


class PublicWebHarvester:
    """Extract explicitly published Place/GeoCoordinates JSON-LD from HTTPS pages.

    Arbitrary text is retained as provenance, not promoted into a location. A
    page without structured coordinates becomes an unlocated review candidate.
    """

    source = "public_web"

    def __init__(self, urls: list[str], cache: HarvestCache | None = None, *,
                 max_pages: int = 10, timeout: float = 10,
                 max_bytes: int = 2_000_000, fetch: Callable | None = None):
        self.urls = list(dict.fromkeys(str(url).strip() for url in urls if str(url).strip()))[:max_pages]
        self.cache = cache
        self.timeout = min(max(float(timeout), 1), 20)
        self.max_bytes = min(max(int(max_bytes), 1024), 2_000_000)
        self.fetch = fetch or self._fetch
        # Injected fetchers are used by offline callers and tests.  Network
        # URL validation remains enforced by the production requests path.
        self._custom_fetch = fetch is not None

    def _validate_url(self, url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("only public HTTPS URLs without credentials are allowed")
        if not _host_is_public(parsed.hostname):
            raise ValueError("URL host must resolve to public IP addresses")
        return url

    def _fetch(self, url: str) -> bytes:
        response = requests.get(url, timeout=self.timeout, allow_redirects=False,
                                headers={"User-Agent": "CampusAssetResearch/1.0"},
                                stream=True)
        if 300 <= response.status_code < 400:
            raise ValueError("redirects are not followed; provide the final public URL")
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "").lower()
        if "text/html" not in content_type and "application/xhtml" not in content_type:
            raise ValueError("URL did not return an HTML page")
        chunks, size = [], 0
        for chunk in response.iter_content(65536):
            size += len(chunk)
            if size > self.max_bytes:
                raise ValueError("HTML response exceeded size limit")
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def _objects(value):
        if isinstance(value, list):
            for item in value:
                yield from PublicWebHarvester._objects(item)
        elif isinstance(value, dict):
            if "@graph" in value:
                yield from PublicWebHarvester._objects(value["@graph"])
            else:
                yield value

    @staticmethod
    def _geo(obj):
        geo = obj.get("geo") or obj.get("location") or {}
        if isinstance(geo, dict):
            try:
                lat = float(geo.get("latitude", geo.get("lat")))
                lng = float(geo.get("longitude", geo.get("lng")))
                if math.isfinite(lat) and math.isfinite(lng):
                    return lng, lat
            except (TypeError, ValueError):
                return None
        return None

    def _collect_url(self, url: str):
        if not self._custom_fetch:
            url = self._validate_url(url)
        cache_parameters = {"url": url, "schemaVersion": 2}
        cached = self.cache.get(self.source, cache_parameters) if self.cache else None
        if cached is not None:
            return cached, []
        content = self.fetch(url)
        if len(content) > self.max_bytes:
            raise ValueError("HTML response exceeded size limit")
        parser = _PageParser()
        parser.feed(content.decode("utf-8", errors="replace"))
        page_text = " ".join(parser.text_parts)[:4000]
        candidates = []
        for raw in parser.json_ld:
            try:
                objects = self._objects(json.loads(raw))
                for obj in objects:
                    name = str(obj.get("name") or "").strip()
                    if not name:
                        continue
                    geo = self._geo(obj)
                    address = obj.get("address") or ""
                    if isinstance(address, dict):
                        address = ", ".join(str(address.get(key) or "") for key in
                                             ("streetAddress", "addressLocality", "addressRegion") if address.get(key))
                    item = {
                        "name": name,
                        "locationName": name,
                        "category": "scene",
                        "subCategory": str(obj.get("@type") or "网页地点"),
                        "tags": ["公开网页"],
                        "text": " ".join(part for part in (name, str(address), page_text[:800]) if part),
                        "source": self.source,
                        "sourceUrls": [url],
                        "confidence": 0.82 if geo else 0.35,
                        "verified": False,
                        "evidence": {"provider": "JSON-LD", "pageTitle": name,
                                     "pageExcerpt": page_text[:800], "address": str(address)},
                    }
                    if geo:
                        item["lng"], item["lat"] = geo
                        # Schema.org GeoCoordinates use geographic WGS84
                        # coordinates unless a publisher explicitly says otherwise.
                        item["coordinateSystem"] = "WGS84"
                    candidates.append(item)
            except (ValueError, TypeError):
                continue
        if not candidates and page_text:
            candidates.append({
                "name": "网页地点线索",
                "locationName": "",
                "category": "scene",
                "subCategory": "待抽取地点",
                "text": page_text[:1200],
                "tags": ["公开网页", "待人工确认"],
                "source": self.source,
                "sourceUrls": [url],
                "confidence": 0.2,
                "verified": False,
                "evidence": {"provider": "HTML text", "pageExcerpt": page_text[:1200]},
            })
        if self.cache:
            self.cache.put(self.source, cache_parameters, candidates)
        return candidates, []

    def collect(self) -> HarvestResult:
        candidates, warnings = [], []
        for url in self.urls:
            try:
                page_candidates, page_warnings = self._collect_url(url)
                candidates.extend(page_candidates)
                warnings.extend(page_warnings)
            except (requests.RequestException, OSError, ValueError) as exc:
                warnings.append(f"web URL {url!r}: {type(exc).__name__}: {exc}")
        return HarvestResult(candidates, self.source, warnings)
