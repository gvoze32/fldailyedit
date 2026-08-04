"""Source-only SortitoutSI player profile parsing and retrieval."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from html.parser import HTMLParser
import json
import re
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

import aiohttp

from scraper.sortitoutsi import SORTITOUTSI_HEADERS


_ALLOWED_HOSTS = frozenset({"sortitoutsi.net", "www.sortitoutsi.net"})
_PERSON_PATH_RE = re.compile(
    r"^/football-manager-data-update/person/(?P<person_id>[0-9]+)"
    r"(?:/(?P<slug>[a-z0-9]+(?:-[a-z0-9]+)*))?$"
)
_POSITION_SEPARATOR_RE = re.compile(r"\s*[,;|]\s*")
_CHARSET_RE = re.compile(r"(?:^|;)\s*charset\s*=\s*[\"']?([^;\"'\s]+)", re.I)
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_UNAVAILABLE_MESSAGE = "SortitoutSI profile is unavailable"
_INVALID_URL_MESSAGE = "Invalid SortitoutSI person URL"


class DraftSourceError(ValueError):
    """Raised when a SortitoutSI URL or profile cannot produce trusted source data."""


@dataclass(frozen=True, slots=True)
class PlayerDraftSource:
    """Metadata stated by the source, without inferred gameplay attributes."""

    sortitoutsi_id: int
    name: str
    profile_url: str
    date_of_birth: str | None
    nationality: str | None
    positions: tuple[str, ...]
    current_club: str | None


def parse_sortitoutsi_person_url(url: str) -> tuple[int, str]:
    """Validate and canonicalize a public SortitoutSI person URL."""

    if not isinstance(url, str) or not url or url != url.strip():
        raise DraftSourceError(_INVALID_URL_MESSAGE)

    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError):
        raise DraftSourceError(_INVALID_URL_MESSAGE) from None

    host = parsed.hostname
    if (
        parsed.scheme != "https"
        or host not in _ALLOWED_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
    ):
        raise DraftSourceError(_INVALID_URL_MESSAGE)

    path_match = _PERSON_PATH_RE.fullmatch(parsed.path)
    if path_match is None:
        raise DraftSourceError(_INVALID_URL_MESSAGE)

    person_id = int(path_match.group("person_id"))
    canonical_url = urlunsplit(("https", host, parsed.path, "", ""))
    return person_id, canonical_url


def _normalize_text(value: str) -> str:
    return " ".join(value.split())


class _ProfileHTMLParser(HTMLParser):
    """Collect structured scripts, identity links, and deterministic label/value pairs."""

    _TEXT_TAGS = frozenset({"h1", "th", "td", "dt", "dd"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.canonical_urls: list[str] = []
        self.json_ld_scripts: list[str] = []
        self.text_elements: list[tuple[str, str]] = []
        self.item_properties: list[tuple[str, str]] = []
        self.labeled_elements: list[tuple[str, str]] = []
        self._captures: list[dict[str, Any]] = []
        self._json_ld_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name.lower(): value for name, value in attrs}

        if tag == "link":
            rel = (attributes.get("rel") or "").lower().split()
            href = attributes.get("href")
            if "canonical" in rel and href:
                self.canonical_urls.append(href)

        if tag == "meta":
            item_property = attributes.get("itemprop")
            content = attributes.get("content")
            if item_property and content:
                self.item_properties.append((item_property, content))

        if tag == "script" and (attributes.get("type") or "").lower().split(";", 1)[0].strip() == "application/ld+json":
            self._json_ld_parts = []
            return

        capture_kind: str | None = None
        capture_key = ""
        if tag in self._TEXT_TAGS:
            capture_kind = "text"
            capture_key = tag
        elif attributes.get("itemprop"):
            capture_kind = "itemprop"
            capture_key = attributes["itemprop"] or ""
        elif attributes.get("data-label"):
            capture_kind = "label"
            capture_key = attributes["data-label"] or ""

        if capture_kind is not None:
            self._captures.append(
                {"tag": tag, "kind": capture_kind, "key": capture_key, "parts": []}
            )

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._json_ld_parts is not None:
            self._json_ld_parts.append(data)
        for capture in self._captures:
            capture["parts"].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._json_ld_parts is not None:
            self.json_ld_scripts.append("".join(self._json_ld_parts))
            self._json_ld_parts = None
            return

        capture_index = next(
            (
                index
                for index in range(len(self._captures) - 1, -1, -1)
                if self._captures[index]["tag"] == tag
            ),
            None,
        )
        if capture_index is None:
            return

        capture = self._captures.pop(capture_index)
        value = _normalize_text("".join(capture["parts"]))
        if not value:
            return
        if capture["kind"] == "text":
            self.text_elements.append((capture["key"], value))
        elif capture["kind"] == "itemprop":
            self.item_properties.append((capture["key"], value))
        else:
            self.labeled_elements.append((capture["key"], value))


def _iter_json_objects(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, list):
        for item in value:
            yield from _iter_json_objects(item)
    elif isinstance(value, dict):
        yield value
        graph = value.get("@graph")
        if graph is not None:
            yield from _iter_json_objects(graph)


def _is_person_object(value: dict[str, Any]) -> bool:
    object_type = value.get("@type")
    if isinstance(object_type, str):
        return object_type.lower() == "person"
    if isinstance(object_type, list):
        return any(
            isinstance(item, str) and item.lower() == "person" for item in object_type
        )
    return False


def _structured_text(value: Any) -> str | None:
    if isinstance(value, str):
        normalized = _normalize_text(value)
        return normalized or None
    if isinstance(value, dict):
        for key in ("name", "@value"):
            text = _structured_text(value.get(key))
            if text:
                return text
    if isinstance(value, list):
        values = [text for item in value if (text := _structured_text(item))]
        if values:
            return ", ".join(values)
    return None


def _structured_positions(value: Any) -> tuple[str, ...]:
    if isinstance(value, list):
        candidates = value
    else:
        candidates = [value]

    positions: list[str] = []
    for candidate in candidates:
        text = _structured_text(candidate)
        if not text:
            continue
        for position in _POSITION_SEPARATOR_RE.split(text):
            normalized = _normalize_text(position)
            if normalized and normalized not in positions:
                positions.append(normalized)
    return tuple(positions)


def _normalized_label(label: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", label.lower()))


def _labeled_values(parser: _ProfileHTMLParser) -> dict[str, str]:
    values: dict[str, str] = {}
    pending: tuple[str, str] | None = None
    for tag, text in parser.text_elements:
        if tag in {"th", "dt"}:
            pending = (tag, text)
        elif pending is not None and (
            (pending[0] == "th" and tag == "td")
            or (pending[0] == "dt" and tag == "dd")
        ):
            values.setdefault(_normalized_label(pending[1]), text)
            pending = None
        elif tag in {"th", "dt"}:
            pending = None

    for label, value in parser.labeled_elements:
        values.setdefault(_normalized_label(label), value)
    return values


def _first_labeled_value(values: dict[str, str], labels: Iterable[str]) -> str | None:
    for label in labels:
        value = values.get(label)
        if value:
            return value
    return None


def _unavailable() -> DraftSourceError:
    return DraftSourceError(_UNAVAILABLE_MESSAGE)


def _profile_identity_urls(
    parser: _ProfileHTMLParser,
    person_objects: Iterable[dict[str, Any]],
) -> list[str]:
    urls = list(parser.canonical_urls)
    for person in person_objects:
        for key in ("@id", "url", "mainEntityOfPage"):
            value = person.get(key)
            if isinstance(value, str):
                urls.append(value)
            elif isinstance(value, dict):
                nested_url = value.get("@id") or value.get("url")
                if isinstance(nested_url, str):
                    urls.append(nested_url)
    return urls


def parse_sortitoutsi_player_profile(
    html: str,
    canonical_url: str,
    sortitoutsi_id: int,
) -> PlayerDraftSource:
    """Extract stated player metadata, preferring JSON-LD over labeled HTML."""

    if not isinstance(html, str):
        raise _unavailable()

    lowered_html = html.lower()
    challenge_markers = (
        "cf-chl-",
        "/cdn-cgi/challenge-platform/",
        "checking your browser",
        "<title>just a moment",
    )
    is_login = bool(
        re.search(r"<input\b[^>]*\btype\s*=\s*[\"']?password\b", lowered_html)
        or re.search(r"<title>\s*(?:log[ -]?in|sign[ -]?in)\b", lowered_html)
    )
    if any(marker in lowered_html for marker in challenge_markers) or is_login:
        raise _unavailable()

    try:
        canonical_id, normalized_canonical_url = parse_sortitoutsi_person_url(
            canonical_url
        )
    except DraftSourceError:
        raise _unavailable() from None
    if canonical_id != sortitoutsi_id:
        raise _unavailable()

    parser = _ProfileHTMLParser()
    try:
        parser.feed(html)
        parser.close()
    except (UnicodeError, ValueError):
        raise _unavailable() from None

    person_objects: list[dict[str, Any]] = []
    for script in parser.json_ld_scripts:
        try:
            structured_data = json.loads(script)
        except (json.JSONDecodeError, TypeError):
            continue
        person_objects.extend(
            value for value in _iter_json_objects(structured_data) if _is_person_object(value)
        )

    identity_urls = _profile_identity_urls(parser, person_objects)
    if not identity_urls:
        raise _unavailable()
    identity_ids: list[int] = []
    for identity_url in identity_urls:
        try:
            identity_id, _ = parse_sortitoutsi_person_url(identity_url)
        except DraftSourceError:
            raise _unavailable() from None
        identity_ids.append(identity_id)
    if any(identity_id != sortitoutsi_id for identity_id in identity_ids):
        raise _unavailable()

    person = person_objects[0] if person_objects else {}
    labeled = _labeled_values(parser)
    item_properties = {
        _normalized_label(name): value for name, value in parser.item_properties
    }
    headings = [text for tag, text in parser.text_elements if tag == "h1"]

    name = (
        _structured_text(person.get("name"))
        or item_properties.get("name")
        or _first_labeled_value(labeled, ("name", "player name"))
        or (headings[0] if headings else None)
    )
    if not name:
        raise _unavailable()

    date_of_birth = (
        _structured_text(person.get("birthDate"))
        or item_properties.get("birthdate")
        or _first_labeled_value(labeled, ("date of birth", "birth date", "dob", "born"))
    )
    nationality = (
        _structured_text(person.get("nationality"))
        or item_properties.get("nationality")
        or _first_labeled_value(labeled, ("nationality", "nation"))
    )
    positions = (
        _structured_positions(person.get("jobTitle"))
        or _structured_positions(person.get("position"))
        or _structured_positions(person.get("positions"))
        or _structured_positions(item_properties.get("jobtitle"))
        or _structured_positions(
            _first_labeled_value(
                labeled,
                ("positions", "position", "playing positions", "playing position"),
            )
        )
    )
    current_club = (
        _structured_text(person.get("memberOf"))
        or _structured_text(person.get("affiliation"))
        or _structured_text(person.get("worksFor"))
        or item_properties.get("memberof")
        or item_properties.get("affiliation")
        or _first_labeled_value(labeled, ("current club", "club", "team"))
    )

    return PlayerDraftSource(
        sortitoutsi_id=sortitoutsi_id,
        name=name,
        profile_url=normalized_canonical_url,
        date_of_birth=date_of_birth,
        nationality=nationality,
        positions=positions,
        current_club=current_club,
    )


def _validate_response_url_chain(response: aiohttp.ClientResponse, person_id: int) -> str:
    final_url = ""
    for item in (*getattr(response, "history", ()), response):
        try:
            response_id, normalized_url = parse_sortitoutsi_person_url(str(item.url))
        except DraftSourceError:
            raise _unavailable() from None
        if response_id != person_id:
            raise _unavailable()
        final_url = normalized_url
    return final_url


def _response_charset(content_type: str) -> str:
    match = _CHARSET_RE.search(content_type)
    return match.group(1) if match else "utf-8"


async def fetch_sortitoutsi_player_profile(url: str) -> PlayerDraftSource:
    """Fetch and parse one allowlisted SortitoutSI person profile."""

    person_id, requested_url = parse_sortitoutsi_person_url(url)
    timeout = aiohttp.ClientTimeout(total=30)

    try:
        async with aiohttp.ClientSession(
            headers=SORTITOUTSI_HEADERS,
            timeout=timeout,
        ) as session:
            async with session.get(requested_url, allow_redirects=True) as response:
                if response.status != 200:
                    raise _unavailable()

                final_url = _validate_response_url_chain(response, person_id)
                content_type = response.headers.get("Content-Type", "")
                media_type = content_type.split(";", 1)[0].strip().lower()
                if media_type not in {"text/html", "application/xhtml+xml"}:
                    raise _unavailable()

                declared_length = response.headers.get("Content-Length")
                if declared_length is not None:
                    try:
                        parsed_length = int(declared_length)
                    except (TypeError, ValueError):
                        raise _unavailable() from None
                    if parsed_length < 0 or parsed_length > _MAX_RESPONSE_BYTES:
                        raise _unavailable()

                body = bytearray()
                async for chunk in response.content.iter_chunked(64 * 1024):
                    if len(body) + len(chunk) > _MAX_RESPONSE_BYTES:
                        raise _unavailable()
                    body.extend(chunk)

                try:
                    html = body.decode(_response_charset(content_type))
                except (LookupError, UnicodeDecodeError):
                    raise _unavailable() from None

        return parse_sortitoutsi_player_profile(html, final_url, person_id)
    except DraftSourceError:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError):
        raise _unavailable() from None


__all__ = [
    "DraftSourceError",
    "PlayerDraftSource",
    "fetch_sortitoutsi_player_profile",
    "parse_sortitoutsi_person_url",
    "parse_sortitoutsi_player_profile",
]
