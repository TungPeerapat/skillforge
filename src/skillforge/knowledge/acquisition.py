"""Consent-gated, bounded acquisition of verified documentation (K7).

No repository-provided URL is followed automatically. Acquisition is sequential
(one worker), respects robots.txt, and redirects stay on the verified host.
The optional httpx adapter pins a publicly resolved IP with TLS SNI, preventing
a second DNS lookup from rebinding a previously checked hostname.
"""

from __future__ import annotations

import hashlib
import json
import socket
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from ipaddress import ip_address
from pathlib import Path
from typing import Protocol
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

from skillforge import __version__
from skillforge.knowledge.authority import is_community_host
from skillforge.knowledge.normalize import NormalizedDocument, normalize_document
from skillforge.models import KnowledgeSource
from skillforge.security.paths import safe_join
from skillforge.utils.fs import atomic_write_text

ALLOWED_TYPES = frozenset(
    {
        "text/html",
        "text/markdown",
        "text/plain",
        "application/json",
        "application/xml",
        "application/yaml",
    }
)


class AcquisitionError(ValueError):
    """A source could not be acquired safely; callers record the reason."""


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes = b""


class HttpTransport(Protocol):
    def get(
        self,
        url: str,
        *,
        timeout: float,
        max_bytes: int,
        headers: Mapping[str, str] | None = None,
    ) -> HttpResponse: ...


def checked_host(url: str) -> str:
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        if (
            parsed.scheme != "https"
            or not host
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
            or "\\" in url
            or any(ord(char) < 32 for char in url)
        ):
            raise AcquisitionError("only HTTPS public-source URLs without credentials are allowed")
        if host == "localhost" or host.endswith((".local", ".internal", ".localhost")):
            raise AcquisitionError("local hosts are refused")
        if is_community_host(host):
            raise AcquisitionError("community sources are refused")
        try:
            address = ip_address(host)
        except ValueError:
            pass
        else:
            if not address.is_global or address.is_multicast:
                raise AcquisitionError("non-public IP addresses are refused")
        return host.lower()
    except ValueError as exc:
        raise AcquisitionError(str(exc)) from exc


class HttpxTransport:
    """Lazy optional adapter; no proxies, cookies, redirects or second DNS lookup."""

    def get(
        self,
        url: str,
        *,
        timeout: float,
        max_bytes: int,
        headers: Mapping[str, str] | None = None,
    ) -> HttpResponse:
        host = checked_host(url)
        try:
            import httpx
        except ImportError as exc:
            raise AcquisitionError("install skillforge[knowledge] for network acquisition") from exc
        addresses = sorted(
            {record[4][0] for record in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
        )
        if not addresses or any(
            not ip_address(address).is_global or ip_address(address).is_multicast
            for address in addresses
        ):
            raise AcquisitionError("DNS resolved to a non-public address")
        address = addresses[0]
        pinned = httpx.URL(url).copy_with(host=address)
        request_headers = {
            "User-Agent": f"skillforge/{__version__}",
            "Host": host,
            "Accept-Encoding": "identity",
            **(headers or {}),
        }
        try:
            with (
                httpx.Client(trust_env=False, follow_redirects=False) as client,
                client.stream(
                    "GET",
                    pinned,
                    headers=request_headers,
                    timeout=httpx.Timeout(timeout, connect=min(timeout, 10)),
                    extensions={"sni_hostname": host},
                ) as response,
            ):
                body = bytearray()
                for chunk in response.iter_bytes(chunk_size=16_384):
                    if len(body) + len(chunk) > max_bytes:
                        raise AcquisitionError("response exceeded the byte budget")
                    body.extend(chunk)
                return HttpResponse(response.status_code, dict(response.headers), bytes(body))
        except httpx.HTTPError as exc:
            raise AcquisitionError(f"HTTP transport failed: {type(exc).__name__}") from exc


@dataclass
class AcquisitionResult:
    document: NormalizedDocument | None = None
    from_cache: bool = False
    reason: str = ""
    rejection_kind: str = ""


@dataclass
class CacheEntryMeta:
    source_id: str
    url: str
    version: str
    content_type: str
    body_hash: str
    etag: str = ""
    last_modified: str = ""
    retrieved_at: str = ""


@dataclass
class DocumentAcquirer:
    cache: Path
    allow_network: bool = False
    transport: HttpTransport = field(default_factory=HttpxTransport)
    max_bytes: int = 2 * 1024 * 1024
    max_pages: int = 200
    min_host_interval: float = 1.0
    _last_request: dict[str, float] = field(default_factory=dict, init=False)
    _robots: dict[str, RobotFileParser] = field(default_factory=dict, init=False)
    _pages: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        if not 1 <= self.max_bytes <= 20 * 1024 * 1024 or self.max_pages < 1:
            raise ValueError("invalid acquisition budgets")
        if self.min_host_interval < 0:
            raise ValueError("host interval cannot be negative")

    def _cache_path(self, source: KnowledgeSource) -> Path:
        key = hashlib.sha256(f"{source.id}:{source.version}:{source.url}".encode()).hexdigest()
        return safe_join(self.cache, f"sha256/{key[:2]}/{key}.json")

    def _load(self, source: KnowledgeSource) -> tuple[dict[str, str], bytes] | None:
        path = self._cache_path(source)
        try:
            # Cache is data too: bound reads and verify identity and content.
            with path.open("rb") as handle:
                encoded = handle.read(self.max_bytes * 6 + 8193)
            if len(encoded) > self.max_bytes * 6 + 8192:
                return None
            data = json.loads(encoded)
            meta = data["meta"]
            body = data["body"].encode("utf-8")
            if (
                meta["source_id"] != source.id
                or meta["url"] != source.url
                or meta["version"] != source.version
                or len(body) > self.max_bytes
                or meta["content_type"] not in ALLOWED_TYPES
                or hashlib.sha256(body).hexdigest() != meta["body_hash"]
            ):
                return None
            return meta, body
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return None

    def _get(self, url: str, headers: Mapping[str, str] | None = None) -> HttpResponse:
        host = checked_host(url)
        wait = self.min_host_interval - (time.monotonic() - self._last_request.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        try:
            response = self.transport.get(
                url, timeout=30, max_bytes=self.max_bytes, headers=headers
            )
            if len(response.body) > self.max_bytes:
                raise AcquisitionError("response exceeded the byte budget")
            return response
        finally:
            self._last_request[host] = time.monotonic()

    def _robots_allowed(self, url: str) -> bool:
        host = checked_host(url)
        if host not in self._robots:
            origin = urlsplit(url)
            robots_url = f"https://{origin.netloc}/robots.txt"
            response = self._get(robots_url)
            parser = RobotFileParser()
            if response.status == 404:
                parser.parse([])
            elif response.status == 200:
                parser.parse(response.body.decode("utf-8", errors="replace").splitlines())
            else:
                parser.parse(["User-agent: *", "Disallow: /"])
            self._robots[host] = parser
        return self._robots[host].can_fetch(f"skillforge/{__version__}", url)

    def acquire(self, source: KnowledgeSource) -> AcquisitionResult:
        """Offline reads never touch DNS or transport, even on a cache miss."""
        if (
            not source.accepted
            or source.rejections
            or not source.authority.is_official
            or not source.verification
        ):
            return AcquisitionResult(
                reason="source is not verified official", rejection_kind="rejected_unknown_host"
            )
        try:
            host = checked_host(source.url)
            cached = self._load(source)
            if not self.allow_network:
                if cached is None:
                    return AcquisitionResult(reason="offline: no valid cached document")
                return self._normalized(source, cached[0], cached[1], from_cache=True)
            if self._pages >= self.max_pages:
                return AcquisitionResult(reason="page budget reached")
            self._pages += 1
            headers: dict[str, str] = {}
            if cached:
                if cached[0].get("etag"):
                    headers["If-None-Match"] = cached[0]["etag"]
                if cached[0].get("last_modified"):
                    headers["If-Modified-Since"] = cached[0]["last_modified"]
            current = source.url
            for redirect in range(4):
                if not self._robots_allowed(current):
                    return AcquisitionResult(
                        reason="robots.txt disallows this URL", rejection_kind="rejected_robots"
                    )
                response = self._get(current, headers)
                response_headers = {key.lower(): value for key, value in response.headers.items()}
                if response.status in {301, 302, 303, 307, 308}:
                    location = response_headers.get("location", "")
                    if not location or redirect == 3:
                        raise AcquisitionError("redirect limit or missing Location")
                    current = urljoin(current, location)
                    if checked_host(current) != host:
                        raise AcquisitionError("redirect left the verified host")
                    continue
                if response.status == 304 and cached:
                    return self._normalized(source, cached[0], cached[1], from_cache=True)
                if response.status != 200:
                    raise AcquisitionError(f"HTTP status {response.status}")
                content_type = (
                    response_headers.get("content-type", "").split(";", 1)[0].strip().lower()
                )
                if content_type not in ALLOWED_TYPES:
                    return AcquisitionResult(
                        reason="unsupported content type", rejection_kind="rejected_content_type"
                    )
                text = response.body.decode("utf-8", errors="strict")
                if "\x00" in text or text.startswith(("MZ", "PK\x03\x04")):
                    return AcquisitionResult(
                        reason="binary content refused", rejection_kind="rejected_content_type"
                    )
                # Normalize before caching so malformed documents cannot poison a cache hit.
                meta = {
                    "source_id": source.id,
                    "url": source.url,
                    "version": source.version,
                    "content_type": content_type,
                    "body_hash": hashlib.sha256(response.body).hexdigest(),
                    "etag": response_headers.get("etag", ""),
                    "last_modified": response_headers.get("last-modified", ""),
                    "retrieved_at": datetime.now(UTC).isoformat(),
                }
                result = self._normalized(source, meta, response.body)
                atomic_write_text(
                    self._cache_path(source),
                    json.dumps(
                        {
                            "meta": meta,
                            "body": text,
                            "normalized": result.document.model_dump(mode="json")
                            if result.document
                            else None,
                        }
                    ),
                )
                return result
            raise AcquisitionError("redirect limit reached")
        except (OSError, ValueError, RecursionError) as exc:
            return AcquisitionResult(reason=f"acquisition refused: {exc}")

    @staticmethod
    def _normalized(
        source: KnowledgeSource,
        meta: Mapping[str, str],
        body: bytes,
        *,
        from_cache: bool = False,
    ) -> AcquisitionResult:
        return AcquisitionResult(
            document=normalize_document(
                body.decode("utf-8"),
                content_type=meta["content_type"],
                source_id=source.id,
                url=source.url,
                version_match=source.version_match,
            ),
            from_cache=from_cache,
        )
