"""Offline fake-transport checks: consent, robots, redirects, cache and budgets."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from skillforge.config import load_settings
from skillforge.errors import ConfigError
from skillforge.knowledge.acquisition import (
    DocumentAcquirer,
    HttpResponse,
    HttpxTransport,
    checked_host,
)
from skillforge.knowledge.normalize import discover_document_urls, normalize_document
from skillforge.models import (
    KnowledgeSource,
    SourceAuthority,
    SourceVerification,
    SourceVerificationKind,
    VersionMatchKind,
)


class FakeTransport:
    def __init__(self, responses: dict[str, HttpResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get(self, url, *, timeout, max_bytes, headers=None):
        self.calls.append((url, dict(headers or {})))
        return self.responses[url]


def source() -> KnowledgeSource:
    return KnowledgeSource(
        id="src-test",
        technology="demo",
        version="1.0.0",
        url="https://docs.example.com/1.0.0/",
        authority=SourceAuthority.EXACT_DOCS,
        version_match=VersionMatchKind.EXACT,
        verification=[SourceVerification(kind=SourceVerificationKind.REGISTRY_DOMAIN_MAP)],
    )


def transport() -> FakeTransport:
    return FakeTransport(
        {
            "https://docs.example.com/robots.txt": HttpResponse(404, {}),
            source().url: HttpResponse(
                200, {"content-type": "text/markdown", "etag": '"v1"'}, b"# API\nHello."
            ),
        }
    )


def acquirer(tmp_path: Path, fake: FakeTransport, **kwargs) -> DocumentAcquirer:
    return DocumentAcquirer(tmp_path, transport=fake, min_host_interval=0, **kwargs)


def test_offline_does_not_contact_transport(tmp_path: Path) -> None:
    fake = transport()
    result = acquirer(tmp_path, fake).acquire(source())
    assert result.document is None
    assert "offline" in result.reason
    assert not fake.calls


def test_online_then_verified_offline_cache(tmp_path: Path) -> None:
    fake = transport()
    online = acquirer(tmp_path, fake, allow_network=True).acquire(source())
    assert online.document is not None
    assert online.document.title == "API"
    fake.calls.clear()
    offline = acquirer(tmp_path, fake).acquire(source())
    assert offline.from_cache
    assert offline.document == online.document
    assert not fake.calls


def test_cache_tampering_is_refused(tmp_path: Path) -> None:
    fake = transport()
    acquirer(tmp_path, fake, allow_network=True).acquire(source())
    path = next(tmp_path.rglob("*.json"))
    data = json.loads(path.read_text())
    data["body"] = "poisoned"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert acquirer(tmp_path, fake).acquire(source()).document is None


def test_etag_revalidation(tmp_path: Path) -> None:
    fake = transport()
    client = acquirer(tmp_path, fake, allow_network=True)
    first = client.acquire(source())
    fake.responses[source().url] = HttpResponse(304, {})
    second = client.acquire(source())
    assert second.from_cache and second.document == first.document
    assert fake.calls[-1][1]["If-None-Match"] == '"v1"'
    assert len([call for call in fake.calls if call[0].endswith("robots.txt")]) == 1


def test_robots_is_respected(tmp_path: Path) -> None:
    fake = transport()
    fake.responses["https://docs.example.com/robots.txt"] = HttpResponse(
        200, {}, b"User-agent: *\nDisallow: /1.0.0/\n"
    )
    result = acquirer(tmp_path, fake, allow_network=True).acquire(source())
    assert result.rejection_kind == "rejected_robots"
    assert len(fake.calls) == 1


def test_cross_host_redirect_is_never_followed(tmp_path: Path) -> None:
    fake = transport()
    fake.responses[source().url] = HttpResponse(302, {"location": "https://127.0.0.1/"})
    result = acquirer(tmp_path, fake, allow_network=True).acquire(source())
    assert result.document is None
    assert all("127.0.0.1" not in call[0] for call in fake.calls)


def test_byte_budget_and_content_type(tmp_path: Path) -> None:
    fake = transport()
    client = acquirer(tmp_path, fake, allow_network=True, max_bytes=8)
    assert client.acquire(source()).document is None
    fake.responses[source().url] = HttpResponse(200, {"content-type": "application/zip"}, b"PK")
    assert (
        acquirer(tmp_path, fake, allow_network=True).acquire(source()).rejection_kind
        == "rejected_content_type"
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/",
        "https://localhost/",
        "https://10.0.0.1/",
        "https://user:pass@example.com/",
        "https://example.com:8443/",
        "https://[::1]/",
    ],
)
def test_unsafe_urls_refused(url: str) -> None:
    with pytest.raises(ValueError):
        checked_host(url)


def test_repository_cannot_grant_network(tmp_path: Path) -> None:
    (tmp_path / "skillforge.toml").write_text("[knowledge]\nallow_network = true\n")
    with pytest.raises(ConfigError, match=r"cannot set knowledge\.allow_network"):
        load_settings(tmp_path, env={})


def test_user_network_consent_is_accepted(tmp_path: Path) -> None:
    config = tmp_path / "user.toml"
    config.write_text("[knowledge]\nallow_network = true\n")
    repo = tmp_path / "repo"
    repo.mkdir()
    assert load_settings(repo, env={"SKILLFORGE_USER_CONFIG": str(config)}).knowledge.allow_network


def test_html_normalizer_keeps_main_heading_and_code() -> None:
    result = normalize_document(
        "<title>Docs</title><nav>Noise</nav><main><h1>API</h1><script>evil()</script>"
        "<p>Hello</p><pre><code>print('ok')\n</code></pre></main>",
        content_type="text/html",
        source_id="test",
        url=source().url,
    )
    assert result.title == "Docs"
    assert result.sections[0].heading == "API"
    assert result.sections[0].code_blocks == ["print('ok')\n"]
    assert "evil" not in result.model_dump_json() and "Noise" not in result.model_dump_json()


def test_page_budget(tmp_path: Path) -> None:
    client = acquirer(tmp_path, transport(), allow_network=True, max_pages=1)
    assert client.acquire(source()).document is not None
    assert client.acquire(source()).reason == "page budget reached"


def test_dns_pinning_and_tls_name(monkeypatch) -> None:
    import socket

    import httpx

    seen = []
    real_client = httpx.Client

    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=b"ok")

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))],
    )
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    assert HttpxTransport().get(source().url, timeout=30, max_bytes=100).body == b"ok"
    assert seen[0].url.host == "8.8.8.8"
    assert seen[0].headers["host"] == "docs.example.com"
    assert seen[0].extensions["sni_hostname"] == "docs.example.com"


def test_private_dns_is_rejected_before_connection(monkeypatch) -> None:
    import socket

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
    )
    with pytest.raises(ValueError, match="non-public"):
        HttpxTransport().get(source().url, timeout=30, max_bytes=100)


def test_discovery_confined_to_official_host() -> None:
    urls = discover_document_urls(
        "[API](/1.0.0/api)\n[evil](https://evil.com/x)\n"
        "[zip](/download.zip)\n[other port](https://docs.example.com:444/api)",
        base_url=source().url,
    )
    assert urls == ["https://docs.example.com/1.0.0/api"]


def test_sitemap_discovery_is_bounded() -> None:
    body = '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    body += "".join(f"<url><loc>https://docs.example.com/{i}</loc></url>" for i in range(10))
    body += "</urlset>"
    assert len(discover_document_urls(body, base_url=source().url, limit=2)) == 2


def test_malformed_xml_does_not_poison_cache(tmp_path: Path) -> None:
    fake = transport()
    fake.responses[source().url] = HttpResponse(
        200, {"content-type": "application/xml"}, b"<broken"
    )
    result = acquirer(tmp_path, fake, allow_network=True).acquire(source())
    assert result.document is None and result.reason
    assert not list(tmp_path.rglob("*.json"))
