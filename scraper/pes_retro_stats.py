"""Bounded Pes Retro Stats player profile parsing and retrieval."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
import json
import re
from types import MappingProxyType
from typing import Any, Iterable, Mapping
from urllib.parse import urljoin, urlsplit, urlunsplit
from uuid import UUID

import aiohttp


_ALLOWED_HOST = "pesretrostats.com"
_PROFILE_PATH_RE = re.compile(
    r"^/player/(?P<short_id>[0-9a-f]{8})-(?P<slug>[a-z0-9]+(?:-[a-z0-9]+)*)$"
)
_INVALID_URL = "Invalid Pes Retro Stats profile URL"
_UNAVAILABLE = "Pes Retro Stats profile is unavailable"
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_REDIRECT_HOPS = 5
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_CHARSET_RE = re.compile(r"(?:^|;)\s*charset\s*=\s*[\"']?([^;\"'\s]+)", re.I)
_HEADERS = {
    "User-Agent": (
        "fldailyedit/0.1 "
        "(Pes Retro Stats player updater; contact via project repository)"
    ),
    "Accept": "text/html, application/xhtml+xml;q=0.9",
}
_STAT_KEYS = (
    "attacking_prowess",
    "technique",
    "dribbling",
    "dribble_accuracy",
    "short_pass_accuracy",
    "long_pass_accuracy",
    "shot_accuracy",
    "heading",
    "free_kick_accuracy",
    "swerve",
    "top_speed",
    "acceleration",
    "shot_power",
    "jump",
    "physical_contact",
    "body_control",
    "stamina",
    "defensive_awareness",
    "ball_winning",
    "new_aggression",
    "gk_awareness",
    "gk_catching",
    "gk_clearing",
    "gk_reflexes",
    "gk_reach",
)
_POSITION_KEYS = (
    "gk",
    "cb",
    "lb",
    "rb",
    "cwp",
    "dmf",
    "lwb",
    "rwb",
    "cmf",
    "lmf",
    "rmf",
    "amf",
    "lwf",
    "rwf",
    "ss",
    "cf",
)
_PROFILE_DISCRIMINATOR_KEYS = frozenset(
    {"id", "name", "birth_date", "attacking_prowess", "position_gk", "player_skills"}
)
_REQUIRED_RECORD_KEYS = frozenset(
    {
        "id",
        "name",
        "full_name",
        "birth_date",
        "nationality",
        "team",
        "shirt_number",
        "height",
        "weight",
        "strong_foot",
        "weak_foot_accuracy",
        "weak_foot_frequency",
        "form",
        "injury_tolerance",
        "playing_style",
        "player_skills",
        "com_playing_styles",
        *(f"position_{key}" for key in _POSITION_KEYS),
        *_STAT_KEYS,
    }
)
_FLIGHT_PUSH_RE = re.compile(
    r"^self\.__next_f\.push\((?P<payload>.*)\);?$", re.DOTALL
)
_FLIGHT_ROW_RE = re.compile(r"^(?P<label>[0-9a-z]+):(?P<value>.*)$", re.DOTALL)
_FLIGHT_REFERENCE_RE = re.compile(r"^\$[0-9A-Za-z]+$")


class PesRetroStatsError(ValueError):
    """Raised when a profile URL or page cannot produce trusted source data."""


@dataclass(frozen=True, slots=True)
class PesRetroStatsProfile:
    player_id: str
    short_id: str
    name: str
    full_name: str | None
    profile_url: str
    birth_date: date
    nationality: str
    current_club: str
    shirt_number: int | None
    height: int
    weight: int
    strong_foot: str
    weak_foot_accuracy: int
    weak_foot_frequency: int
    form: int
    injury_tolerance: str
    playing_style: str | None
    positions: Mapping[str, str | None]
    stats: Mapping[str, int]
    player_skill_codes: tuple[str, ...]
    com_playing_styles: tuple[str, ...]


def parse_pes_retro_stats_url(url: str) -> tuple[str, str]:
    """Validate one canonical public Pes Retro Stats profile URL."""

    if not isinstance(url, str) or not url or url != url.strip():
        raise PesRetroStatsError(_INVALID_URL)
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError):
        raise PesRetroStatsError(_INVALID_URL) from None
    match = _PROFILE_PATH_RE.fullmatch(parsed.path)
    if (
        parsed.scheme != "https"
        or parsed.hostname != _ALLOWED_HOST
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
        or match is None
    ):
        raise PesRetroStatsError(_INVALID_URL)
    canonical = urlunsplit(("https", _ALLOWED_HOST, parsed.path, "", ""))
    if canonical != url:
        raise PesRetroStatsError(_INVALID_URL)
    return match.group("short_id"), canonical


def _unavailable() -> PesRetroStatsError:
    return PesRetroStatsError(_UNAVAILABLE)


class _ProfileHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.canonical_urls: list[str | None] = []
        self.scripts: list[str] = []
        self._script_parts: list[str] | None = None

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attributes = dict(attrs)
        if tag.lower() == "link":
            rel = attributes.get("rel")
            if isinstance(rel, str) and "canonical" in rel.lower().split():
                self.canonical_urls.append(attributes.get("href"))
        elif tag.lower() == "script":
            self._script_parts = []

    def handle_data(self, data: str) -> None:
        if self._script_parts is not None:
            self._script_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._script_parts is not None:
            self.scripts.append("".join(self._script_parts))
            self._script_parts = None


def _iter_json_objects(value: Any) -> Iterable[dict[str, Any]]:
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            yield current
            stack.extend(reversed(tuple(current.values())))
        elif isinstance(current, list):
            stack.extend(reversed(current))


def _flight_values(scripts: Iterable[str]) -> Iterable[Any]:
    decoder = json.JSONDecoder()
    for script in scripts:
        text = script.strip()
        if not text.startswith("self.__next_f.push("):
            continue
        match = _FLIGHT_PUSH_RE.fullmatch(text)
        if match is None:
            raise _unavailable()
        try:
            payload = json.loads(match.group("payload"))
        except (json.JSONDecodeError, RecursionError):
            raise _unavailable() from None
        if (
            not isinstance(payload, list)
            or len(payload) != 2
            or type(payload[0]) is not int
            or payload[0] != 1
            or not isinstance(payload[1], str)
        ):
            continue
        for row in payload[1].splitlines():
            row_match = _FLIGHT_ROW_RE.fullmatch(row)
            if row_match is None:
                continue
            encoded = row_match.group("value").lstrip()
            try:
                value, end = decoder.raw_decode(encoded)
            except (json.JSONDecodeError, RecursionError):
                if row_match.group("label") == "21":
                    raise _unavailable() from None
                continue
            if encoded[end:].strip():
                if row_match.group("label") == "21":
                    raise _unavailable()
                continue
            yield value


def _text(record: Mapping[str, Any], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str):
        raise _unavailable()
    normalized = " ".join(value.split())
    if not normalized:
        raise _unavailable()
    return normalized


def _optional_text(record: Mapping[str, Any], key: str) -> str | None:
    value = record.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise _unavailable()
    normalized = " ".join(value.split())
    if not normalized:
        raise _unavailable()
    return normalized


def _integer(record: Mapping[str, Any], key: str) -> int:
    value = record.get(key)
    if type(value) is not int:
        raise _unavailable()
    return value


def _optional_integer(record: Mapping[str, Any], key: str) -> int | None:
    value = record.get(key)
    if value is None:
        return None
    if type(value) is not int:
        raise _unavailable()
    return value


def _string_tuple(record: Mapping[str, Any], key: str) -> tuple[str, ...]:
    raw_values = record.get(key)
    if not isinstance(raw_values, list):
        raise _unavailable()
    values: list[str] = []
    for raw_value in raw_values:
        if not isinstance(raw_value, str):
            raise _unavailable()
        value = " ".join(raw_value.split())
        if not value:
            raise _unavailable()
        values.append(value)
    if len(values) != len(set(values)):
        raise _unavailable()
    return tuple(values)


def _normalize_candidate(
    record: dict[str, Any], canonical_url: str, short_id: str
) -> PesRetroStatsProfile:
    if not _REQUIRED_RECORD_KEYS.issubset(record):
        raise _unavailable()

    raw_id = record.get("id")
    if not isinstance(raw_id, str):
        raise _unavailable()
    try:
        parsed_uuid = UUID(raw_id)
    except (ValueError, AttributeError):
        raise _unavailable() from None
    if str(parsed_uuid) != raw_id or raw_id[:8] != short_id:
        raise _unavailable()

    raw_birth_date = record.get("birth_date")
    if not isinstance(raw_birth_date, str):
        raise _unavailable()
    try:
        birth_date = date.fromisoformat(raw_birth_date)
    except ValueError:
        raise _unavailable() from None
    if birth_date.isoformat() != raw_birth_date:
        raise _unavailable()

    team = record.get("team")
    if not isinstance(team, dict):
        raise _unavailable()
    current_club = _text(team, "name")

    positions: dict[str, str | None] = {}
    for position in _POSITION_KEYS:
        source_key = f"position_{position}"
        value = record.get(source_key)
        if value is not None:
            if not isinstance(value, str):
                raise _unavailable()
            value = " ".join(value.split())
            if not value:
                raise _unavailable()
        positions[position.upper()] = value

    stats = {key: _integer(record, key) for key in _STAT_KEYS}
    return PesRetroStatsProfile(
        player_id=raw_id,
        short_id=short_id,
        name=_text(record, "name"),
        full_name=_optional_text(record, "full_name"),
        profile_url=canonical_url,
        birth_date=birth_date,
        nationality=_text(record, "nationality"),
        current_club=current_club,
        shirt_number=_optional_integer(record, "shirt_number"),
        height=_integer(record, "height"),
        weight=_integer(record, "weight"),
        strong_foot=_text(record, "strong_foot"),
        weak_foot_accuracy=_integer(record, "weak_foot_accuracy"),
        weak_foot_frequency=_integer(record, "weak_foot_frequency"),
        form=_integer(record, "form"),
        injury_tolerance=_text(record, "injury_tolerance"),
        playing_style=_optional_text(record, "playing_style"),
        positions=MappingProxyType(positions),
        stats=MappingProxyType(stats),
        player_skill_codes=_string_tuple(record, "player_skills"),
        com_playing_styles=_string_tuple(record, "com_playing_styles"),
    )


def _profile_key(profile: PesRetroStatsProfile) -> tuple[Any, ...]:
    return (
        profile.player_id,
        profile.short_id,
        profile.name,
        profile.full_name,
        profile.profile_url,
        profile.birth_date,
        profile.nationality,
        profile.current_club,
        profile.shirt_number,
        profile.height,
        profile.weight,
        profile.strong_foot,
        profile.weak_foot_accuracy,
        profile.weak_foot_frequency,
        profile.form,
        profile.injury_tolerance,
        profile.playing_style,
        tuple(profile.positions.items()),
        tuple(profile.stats.items()),
        profile.player_skill_codes,
        profile.com_playing_styles,
    )


def parse_pes_retro_stats_profile(
    html: str, canonical_url: str, short_id: str
) -> PesRetroStatsProfile:
    """Extract one structurally complete profile from Next.js flight data."""

    if not isinstance(html, str):
        raise _unavailable()
    try:
        parsed_short_id, normalized_url = parse_pes_retro_stats_url(canonical_url)
    except PesRetroStatsError:
        raise _unavailable() from None
    if parsed_short_id != short_id or normalized_url != canonical_url:
        raise _unavailable()

    parser = _ProfileHTMLParser()
    try:
        parser.feed(html)
        parser.close()
    except (RecursionError, ValueError):
        raise _unavailable() from None
    if set(parser.canonical_urls) != {canonical_url}:
        raise _unavailable()

    profiles: dict[tuple[Any, ...], PesRetroStatsProfile] = {}
    try:
        for value in _flight_values(parser.scripts):
            for record in _iter_json_objects(value):
                if not _PROFILE_DISCRIMINATOR_KEYS.issubset(record):
                    continue
                if not _REQUIRED_RECORD_KEYS.issubset(record):
                    continue
                if any(
                    isinstance(record.get(key), str)
                    and _FLIGHT_REFERENCE_RE.fullmatch(record[key])
                    for key in ("team", "player_skills", "com_playing_styles")
                ):
                    continue
                profile = _normalize_candidate(record, canonical_url, short_id)
                profiles.setdefault(_profile_key(profile), profile)
    except (PesRetroStatsError, RecursionError):
        raise _unavailable() from None
    if len(profiles) != 1:
        raise _unavailable()
    return next(iter(profiles.values()))


def _validated_response_url(
    response: aiohttp.ClientResponse, short_id: str
) -> str:
    try:
        response_short_id, normalized_url = parse_pes_retro_stats_url(
            str(response.url)
        )
    except PesRetroStatsError:
        raise _unavailable() from None
    if response_short_id != short_id:
        raise _unavailable()
    return normalized_url


def _response_charset(content_type: str) -> str:
    match = _CHARSET_RE.search(content_type)
    return match.group(1) if match else "utf-8"


async def fetch_pes_retro_stats_profile(url: str) -> PesRetroStatsProfile:
    """Fetch and parse one allowlisted Pes Retro Stats player profile."""

    short_id, requested_url = parse_pes_retro_stats_url(url)
    timeout = aiohttp.ClientTimeout(total=30)

    try:
        async with aiohttp.ClientSession(headers=_HEADERS, timeout=timeout) as session:
            current_url = requested_url
            visited_urls = {current_url}
            for redirect_count in range(_MAX_REDIRECT_HOPS + 1):
                async with session.get(
                    current_url, allow_redirects=False
                ) as response:
                    response_url = _validated_response_url(response, short_id)
                    if response.status in _REDIRECT_STATUSES:
                        if redirect_count == _MAX_REDIRECT_HOPS:
                            raise _unavailable()
                        location = response.headers.get("Location")
                        if not isinstance(location, str) or not location:
                            raise _unavailable()
                        try:
                            candidate_url = urljoin(response_url, location)
                            redirect_short_id, next_url = (
                                parse_pes_retro_stats_url(candidate_url)
                            )
                        except (PesRetroStatsError, TypeError, ValueError):
                            raise _unavailable() from None
                        if (
                            redirect_short_id != short_id
                            or next_url in visited_urls
                        ):
                            raise _unavailable()
                        visited_urls.add(next_url)
                        current_url = next_url
                        continue

                    if response.status != 200:
                        raise _unavailable()

                    content_type = response.headers.get("Content-Type", "")
                    media_type = content_type.split(";", 1)[0].strip().lower()
                    if media_type not in {
                        "text/html",
                        "application/xhtml+xml",
                    }:
                        raise _unavailable()

                    declared_length = response.headers.get("Content-Length")
                    if declared_length is not None:
                        try:
                            parsed_length = int(declared_length)
                        except (TypeError, ValueError):
                            raise _unavailable() from None
                        if (
                            parsed_length < 0
                            or parsed_length > _MAX_RESPONSE_BYTES
                        ):
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
                    final_url = response_url
                    break
            else:
                raise _unavailable()

        return parse_pes_retro_stats_profile(html, final_url, short_id)
    except PesRetroStatsError:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError):
        raise _unavailable() from None


__all__ = [
    "PesRetroStatsError",
    "PesRetroStatsProfile",
    "fetch_pes_retro_stats_profile",
    "parse_pes_retro_stats_profile",
    "parse_pes_retro_stats_url",
]
