"""Offline document normalization; HTML is parsed as data, never executed."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin, urlsplit

import yaml
from pydantic import BaseModel, Field

from skillforge.models import VersionMatchKind


class NormalizedSection(BaseModel):
    heading: str = ""
    level: int = 0
    text: str = ""
    code_blocks: list[str] = Field(default_factory=list)


class NormalizedDocument(BaseModel):
    source_id: str
    url: str
    title: str = ""
    sections: list[NormalizedSection] = Field(default_factory=list)
    version_hints: list[str] = Field(default_factory=list)
    version_match: VersionMatchKind = VersionMatchKind.UNKNOWN


class _HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.main: list[str] = []
        self.in_main = 0
        self.suppressed = 0
        self.pre = 0
        self.title_parts: list[str] = []
        self.in_title = False

    def emit(self, text: str) -> None:
        self.text.append(text)
        if self.in_main:
            self.main.append(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self.suppressed += 1
        if self.suppressed:
            return
        if tag in {"main", "article"}:
            self.in_main += 1
        if tag == "title":
            self.in_title = True
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.emit("\n" + "#" * int(tag[1]) + " ")
        elif tag == "pre":
            self.pre += 1
            self.emit("\n```\n")
        elif tag in {"p", "div", "li", "br", "section"}:
            self.emit("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self.suppressed = max(0, self.suppressed - 1)
            return
        if self.suppressed:
            return
        if tag == "title":
            self.in_title = False
        if tag == "pre":
            self.pre = max(0, self.pre - 1)
            self.emit("\n```\n")
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li"}:
            self.emit("\n")
        if tag in {"main", "article"}:
            self.in_main = max(0, self.in_main - 1)

    def handle_data(self, data: str) -> None:
        if self.suppressed:
            return
        if self.in_title:
            self.title_parts.append(data)
        else:
            self.emit(data if self.pre else re.sub(r"\s+", " ", data))


def _sections(text: str) -> list[NormalizedSection]:
    sections: list[NormalizedSection] = []
    current = NormalizedSection()
    code: list[str] | None = None
    for line in text.splitlines():
        if line.strip().startswith("```"):
            if code is None:
                code = []
            else:
                current.code_blocks.append("\n".join(code))
                code = None
        elif code is not None:
            code.append(line)
        elif match := re.match(r"^(#{1,6})\s+(.+)", line):
            if current.heading or current.text or current.code_blocks:
                sections.append(current)
            current = NormalizedSection(heading=match[2].strip(), level=len(match[1]))
        elif line.strip():
            current.text += ("\n" if current.text else "") + line.strip()
    if code is not None:
        current.code_blocks.append("\n".join(code))
    if current.heading or current.text or current.code_blocks:
        sections.append(current)
    return sections


def normalize_document(
    body: str,
    *,
    content_type: str,
    source_id: str,
    url: str,
    version_match: VersionMatchKind = VersionMatchKind.UNKNOWN,
) -> NormalizedDocument:
    media_type = content_type.split(";", 1)[0].lower().strip()
    title = ""
    if media_type == "text/html":
        parser = _HTMLText()
        parser.feed(body)
        body = "".join(parser.main or parser.text)
        title = "".join(parser.title_parts).strip()
    elif media_type == "application/json":
        body = json.dumps(json.loads(body), indent=2, sort_keys=True)
    elif media_type == "application/yaml":
        try:
            if any(
                isinstance(token, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken))
                for token in yaml.scan(body)
            ):
                raise ValueError("YAML aliases and anchors are not supported")
            body = json.dumps(yaml.safe_load(body), indent=2, sort_keys=True, default=str)
        except (yaml.YAMLError, RecursionError) as exc:
            raise ValueError("invalid YAML document") from exc
    elif media_type == "application/xml":
        if "<!DOCTYPE" in body.upper() or "<!ENTITY" in body.upper():
            raise ValueError("XML entities and DTDs are not supported")
        try:
            body = "\n".join(
                text.strip() for text in ET.fromstring(body).itertext() if text.strip()
            )
        except ET.ParseError as exc:
            raise ValueError("invalid XML document") from exc
    sections = _sections(body)
    if not title:
        title = next((section.heading for section in sections if section.heading), "")
    return NormalizedDocument(
        source_id=source_id,
        url=url,
        title=title,
        sections=sections,
        version_match=version_match,
        version_hints=sorted(set(re.findall(r"\bv?\d+\.\d+(?:\.\d+)?\b", body)))[:100],
    )


def discover_document_urls(body: str, *, base_url: str, limit: int = 200) -> list[str]:
    """Extract bounded same-host candidates from an API index, sitemap or llms.txt.

    This is not a crawler: callers must still apply source authority, robots,
    consent and the shared page budget before requesting any candidate.
    """

    class Links(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.urls: list[str] = []

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag == "a" and len(self.urls) < limit:
                self.urls.extend(value for key, value in attrs if key == "href" and value)

    if limit < 1:
        return []
    candidates: list[str] = []
    if body.lstrip().startswith("<"):
        if "<!DOCTYPE" not in body.upper() and "<!ENTITY" not in body.upper():
            try:
                for node in ET.fromstring(body).iter():
                    if node.tag.rsplit("}", 1)[-1] == "loc" and node.text:
                        candidates.append(node.text.strip())
                        if len(candidates) >= limit:
                            break
            except ET.ParseError:
                pass
        parser = Links()
        parser.feed(body)
        candidates.extend(parser.urls)
    else:
        candidates.extend(re.findall(r"\[[^\]]*\]\(([^)\s]+)\)", body)[:limit])
    host = urlsplit(base_url).hostname
    found: set[str] = set()
    for candidate in candidates:
        try:
            url = urldefrag(urljoin(base_url, candidate))[0]
            parts = urlsplit(url)
            port = parts.port
        except ValueError:
            continue
        if (
            parts.scheme == "https"
            and parts.hostname == host
            and not parts.username
            and not parts.password
            and port in (None, 443)
            and not parts.path.lower().endswith((".zip", ".tar", ".gz", ".whl", ".jar", ".exe"))
        ):
            found.add(url)
        if len(found) >= limit:
            break
    return sorted(found)
