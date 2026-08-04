import asyncio
from dataclasses import FrozenInstanceError
from datetime import date
import json

import aiohttp

import pytest

from scraper import pes_retro_stats
from scraper.pes_retro_stats import (
    PesRetroStatsError,
    PesRetroStatsProfile,
    fetch_pes_retro_stats_profile,
    parse_pes_retro_stats_profile,
    parse_pes_retro_stats_url,
)


PROFILE_URL = "https://pesretrostats.com/player/0ce2dbde-marco-palestra"
STAT_KEYS = (
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
POSITION_KEYS = (
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


def valid_profile_record() -> dict[str, object]:
    record: dict[str, object] = {
        "id": "0ce2dbde-9cd9-423c-a90a-35b07df6a967",
        "name": "Marco Palestra",
        "full_name": "Marco Palestra",
        "birth_date": "2005-03-03",
        "nationality": "Italy",
        "team": {"name": "Chelsea FC"},
        "shirt_number": 2,
        "height": 186,
        "weight": 80,
        "strong_foot": "R",
        "weak_foot_accuracy": 7,
        "weak_foot_frequency": 7,
        "form": 4,
        "injury_tolerance": "A",
        "playing_style": "Offensive Full-back",
        "player_skills": ["S01", "S07"],
        "com_playing_styles": ["Speeding Bullet", "Mazing Run"],
    }
    record.update({f"position_{key}": None for key in POSITION_KEYS})
    record["position_rb"] = "A"
    record["position_rwb"] = "★"
    record["position_rmf"] = "A"
    record.update({key: 40 + index for index, key in enumerate(STAT_KEYS)})
    return record


def flight_html(*records: dict[str, object], canonical: str = PROFILE_URL) -> str:
    scripts = "".join(
        "<script>self.__next_f.push(" + json.dumps([1, "21:" + json.dumps(record)]) + ")</script>"
        for record in records
    )
    return (
        '<html><head><link rel="canonical" href="'
        + canonical
        + '"></head><body>'
        + scripts
        + "</body></html>"
    )


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://pesretrostats.com/player/0ce2dbde-marco-palestra",
            (
                "0ce2dbde",
                "https://pesretrostats.com/player/0ce2dbde-marco-palestra",
            ),
        ),
        (
            "https://pesretrostats.com/player/f77d9c27-dastan-satpaev",
            (
                "f77d9c27",
                "https://pesretrostats.com/player/f77d9c27-dastan-satpaev",
            ),
        ),
    ],
)
def test_parse_pes_retro_stats_url_accepts_canonical_profiles(url, expected):
    assert parse_pes_retro_stats_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "http://pesretrostats.com/player/0ce2dbde-marco-palestra",
        "https://www.pesretrostats.com/player/0ce2dbde-marco-palestra",
        "https://user@pesretrostats.com/player/0ce2dbde-marco-palestra",
        "https://pesretrostats.com:443/player/0ce2dbde-marco-palestra",
        "https://pesretrostats.com/player/0CE2DBDE-marco-palestra",
        "https://pesretrostats.com/player/0ce2dbde-Marco-Palestra",
        "https://pesretrostats.com/player/0ce2dbde-marco-palestra?source=test",
        "https://pesretrostats.com/player/0ce2dbde-marco-palestra#stats",
        "https://pesretrostats.com/api/player/0ce2dbde",
        "https://pesretrostats.com:/player/0ce2dbde-marco-palestra",
        "https://pesretrostats.com/player/0ce2dbde-marco-palestra?",
        "https://pesretrostats.com/player/0ce2dbde-marco-palestra#",
        "https://pesretrostats.com/\tplayer/0ce2dbde-marco-palestra",
        "https://pesretrostats.com/player/0ce2dbde-marco-\npalestra",
    ],
)
def test_parse_pes_retro_stats_url_rejects_noncanonical_inputs(url):
    with pytest.raises(PesRetroStatsError, match="Invalid Pes Retro Stats profile URL"):
        parse_pes_retro_stats_url(url)


def test_parser_normalizes_a_complete_record_into_immutable_values():
    profile = parse_pes_retro_stats_profile(
        flight_html(valid_profile_record()), PROFILE_URL, "0ce2dbde"
    )

    assert profile.player_id == "0ce2dbde-9cd9-423c-a90a-35b07df6a967"
    assert profile.short_id == "0ce2dbde"
    assert profile.name == "Marco Palestra"
    assert profile.full_name == "Marco Palestra"
    assert profile.profile_url == PROFILE_URL
    assert profile.birth_date == date(2005, 3, 3)
    assert profile.nationality == "Italy"
    assert profile.current_club == "Chelsea FC"
    assert profile.shirt_number == 2
    assert profile.height == 186
    assert profile.weight == 80
    assert profile.strong_foot == "R"
    assert profile.weak_foot_accuracy == 7
    assert profile.weak_foot_frequency == 7
    assert profile.form == 4
    assert profile.injury_tolerance == "A"
    assert profile.playing_style == "Offensive Full-back"
    assert profile.positions == {
        key.upper(): valid_profile_record()[f"position_{key}"] for key in POSITION_KEYS
    }
    assert profile.stats == {
        key: valid_profile_record()[key] for key in STAT_KEYS
    }
    assert profile.player_skill_codes == ("S01", "S07")
    assert profile.com_playing_styles == ("Speeding Bullet", "Mazing Run")
    with pytest.raises(TypeError):
        profile.positions["GK"] = "A"
    with pytest.raises(TypeError):
        profile.stats["stamina"] = 99
    with pytest.raises(FrozenInstanceError):
        profile.name = "Changed"


def test_parser_deduplicates_identical_complete_records():
    record = valid_profile_record()
    profile = parse_pes_retro_stats_profile(
        flight_html(record, dict(record)), PROFILE_URL, "0ce2dbde"
    )
    assert profile.player_id == "0ce2dbde-9cd9-423c-a90a-35b07df6a967"
    assert profile.name == "Marco Palestra"
    assert profile.player_skill_codes == ("S01", "S07")


def test_parser_rejects_two_distinct_complete_records():
    first = valid_profile_record()
    second = {**first, "id": "0ce2dbde-1111-4111-8111-111111111111"}
    with pytest.raises(PesRetroStatsError, match="unavailable"):
        parse_pes_retro_stats_profile(
            flight_html(first, second), PROFILE_URL, "0ce2dbde"
        )


def test_parser_rejects_malformed_flight_json():
    html = (
        '<link rel="canonical" href="'
        + PROFILE_URL
        + '"><script>self.__next_f.push([1, "21:{not json}"])</script>'
    )
    with pytest.raises(PesRetroStatsError, match="unavailable"):
        parse_pes_retro_stats_profile(html, PROFILE_URL, "0ce2dbde")


def test_parser_rejects_an_incomplete_record():
    record = valid_profile_record()
    del record["weight"]
    with pytest.raises(PesRetroStatsError, match="unavailable"):
        parse_pes_retro_stats_profile(flight_html(record), PROFILE_URL, "0ce2dbde")


@pytest.mark.parametrize(
    "player_id",
    [
        "f77d9c27-9cd9-423c-a90a-35b07df6a967",
        "not-a-uuid",
        "0CE2DBDE-9CD9-423C-A90A-35B07DF6A967",
    ],
)
def test_parser_rejects_invalid_or_mismatched_player_ids(player_id):
    record = valid_profile_record()
    record["id"] = player_id
    with pytest.raises(PesRetroStatsError, match="unavailable"):
        parse_pes_retro_stats_profile(flight_html(record), PROFILE_URL, "0ce2dbde")


def test_parser_rejects_a_canonical_link_mismatch():
    with pytest.raises(PesRetroStatsError, match="unavailable"):
        parse_pes_retro_stats_profile(
            flight_html(
                valid_profile_record(),
                canonical="https://pesretrostats.com/player/f77d9c27-dastan-satpaev",
            ),
            PROFILE_URL,
            "0ce2dbde",
        )


@pytest.mark.parametrize(
    ("field", "wrong_value"),
    [
        ("name", 7),
        ("full_name", 7),
        ("birth_date", ["2005-03-03"]),
        ("nationality", None),
        ("shirt_number", True),
        ("height", "186"),
        ("weight", 80.0),
        ("strong_foot", ["R"]),
        ("playing_style", 15),
        ("player_skills", "$23"),
        ("com_playing_styles", {"style": "Mazing Run"}),
        ("team", "$25"),
        ("position_gk", []),
        ("attacking_prowess", True),
    ],
)
def test_parser_rejects_wrong_scalar_list_and_map_types(field, wrong_value):
    record = valid_profile_record()
    record[field] = wrong_value
    with pytest.raises(PesRetroStatsError, match="unavailable"):
        parse_pes_retro_stats_profile(flight_html(record), PROFILE_URL, "0ce2dbde")


def test_parser_rejects_a_malformed_complete_candidate_beside_a_valid_one():
    malformed = {
        **valid_profile_record(),
        "id": "0ce2dbde-1111-4111-8111-111111111111",
        "height": "186",
    }
    with pytest.raises(PesRetroStatsError, match="unavailable"):
        parse_pes_retro_stats_profile(
            flight_html(valid_profile_record(), malformed),
            PROFILE_URL,
            "0ce2dbde",
        )


@pytest.mark.parametrize(
    ("field", "values"),
    [
        ("player_skills", ["S01", "S01"]),
        ("com_playing_styles", ["Mazing Run", "Mazing Run"]),
    ],
)
def test_parser_rejects_duplicate_skill_and_style_entries(field, values):
    record = valid_profile_record()
    record[field] = values
    with pytest.raises(PesRetroStatsError, match="unavailable"):
        parse_pes_retro_stats_profile(flight_html(record), PROFILE_URL, "0ce2dbde")


@pytest.mark.parametrize(
    "html",
    [
        "<html><head><title>Just a moment...</title></head><body>Checking your browser</body></html>",
        '<html><head><title>Login</title></head><body><form action="/login"><input type="password"></form></body></html>',
    ],
)
def test_parser_rejects_login_and_challenge_html(html):
    with pytest.raises(PesRetroStatsError, match="unavailable"):
        parse_pes_retro_stats_profile(html, PROFILE_URL, "0ce2dbde")


def test_parser_ignores_reference_record_when_a_complete_record_exists():
    incomplete = valid_profile_record()
    incomplete["player_skills"] = "$23"
    incomplete["com_playing_styles"] = "$24"

    profile = parse_pes_retro_stats_profile(
        flight_html(incomplete, valid_profile_record()), PROFILE_URL, "0ce2dbde"
    )

    assert profile.player_skill_codes == ("S01", "S07")


class _FakeContent:
    def __init__(self, chunks):
        self._chunks = chunks
        self.requested_sizes = []

    async def iter_chunked(self, size):
        self.requested_sizes.append(size)
        for chunk in self._chunks:
            yield chunk


class _FakeResponse:
    def __init__(
        self,
        body=None,
        *,
        chunks=None,
        url=PROFILE_URL,
        status=200,
        content_type="text/html; charset=utf-8",
        content_length=None,
    ):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.content = _FakeContent(tuple(chunks) if chunks is not None else (data,))
        self.url = url
        self.status = status
        self.headers = {"Content-Type": content_type}
        self.entered = False
        self.exited = False
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self.exited = True
        return False


class _FakeSession:
    responses = ()
    options = None
    requested_urls = None
    allow_redirects = None
    last_instance = None

    def __init__(self, **options):
        type(self).options = options
        type(self).last_instance = self
        self.entered = False
        self.exited = False

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self.exited = True
        return False

    def get(self, url, *, allow_redirects):
        type(self).requested_urls.append(url)
        type(self).allow_redirects.append(allow_redirects)
        response = type(self).responses[len(type(self).requested_urls) - 1]
        if isinstance(response, BaseException):
            raise response
        return response


def _install_response(monkeypatch, *responses):
    _FakeSession.responses = responses
    _FakeSession.options = None
    _FakeSession.requested_urls = []
    _FakeSession.allow_redirects = []
    _FakeSession.last_instance = None
    monkeypatch.setattr(pes_retro_stats.aiohttp, "ClientSession", _FakeSession)


def test_fetch_follows_only_validated_same_host_redirects(monkeypatch):
    redirected_url = PROFILE_URL + "-updated"
    redirect = _FakeResponse(b"", status=302)
    redirect.headers["Location"] = "/player/0ce2dbde-marco-palestra-updated"
    response = _FakeResponse(
        flight_html(valid_profile_record(), canonical=redirected_url),
        url=redirected_url,
    )
    _install_response(monkeypatch, redirect, response)

    profile = asyncio.run(fetch_pes_retro_stats_profile(PROFILE_URL))

    assert profile.profile_url == redirected_url
    assert profile.short_id == "0ce2dbde"
    assert _FakeSession.requested_urls == [PROFILE_URL, redirected_url]
    assert _FakeSession.allow_redirects == [False, False]
    assert _FakeSession.options["headers"] == {
        "User-Agent": "fldailyedit/0.1 (Pes Retro Stats player updater; contact via project repository)",
        "Accept": "text/html, application/xhtml+xml;q=0.9",
    }
    assert _FakeSession.options["timeout"].total == 30
    assert response.content.requested_sizes == [64 * 1024]
    assert redirect.entered and redirect.exited
    assert response.entered and response.exited
    assert _FakeSession.last_instance.entered and _FakeSession.last_instance.exited


@pytest.mark.parametrize(
    "location",
    [
        "https://evil.example/player/0ce2dbde-marco-palestra",
        "https://pesretrostats.com/player/f77d9c27-dastan-satpaev",
    ],
)
def test_fetch_rejects_cross_host_or_changed_identity_redirects(monkeypatch, location):
    redirect = _FakeResponse(b"", status=302)
    redirect.headers["Location"] = location
    _install_response(monkeypatch, redirect)

    with pytest.raises(
        PesRetroStatsError, match="^Pes Retro Stats profile is unavailable$"
    ):
        asyncio.run(fetch_pes_retro_stats_profile(PROFILE_URL))

    assert _FakeSession.requested_urls == [PROFILE_URL]


def test_fetch_rejects_redirect_without_location(monkeypatch):
    _install_response(monkeypatch, _FakeResponse(b"", status=302))

    with pytest.raises(PesRetroStatsError, match="unavailable"):
        asyncio.run(fetch_pes_retro_stats_profile(PROFILE_URL))


def test_fetch_rejects_redirect_loops_with_bounded_requests(monkeypatch):
    second_url = PROFILE_URL + "-updated"
    first = _FakeResponse(b"", url=PROFILE_URL, status=302)
    first.headers["Location"] = second_url
    second = _FakeResponse(b"", url=second_url, status=302)
    second.headers["Location"] = PROFILE_URL
    _install_response(monkeypatch, first, second)

    with pytest.raises(PesRetroStatsError, match="unavailable"):
        asyncio.run(fetch_pes_retro_stats_profile(PROFILE_URL))

    assert _FakeSession.requested_urls == [PROFILE_URL, second_url]
    assert first.exited and second.exited


def test_fetch_rejects_redirect_chains_beyond_five_hops(monkeypatch):
    urls = [PROFILE_URL] + [PROFILE_URL + f"-hop-{index}" for index in range(6)]
    responses = []
    for current_url, next_url in zip(urls, urls[1:]):
        response = _FakeResponse(b"", url=current_url, status=302)
        response.headers["Location"] = next_url
        responses.append(response)
    _install_response(monkeypatch, *responses)

    with pytest.raises(PesRetroStatsError, match="unavailable"):
        asyncio.run(fetch_pes_retro_stats_profile(PROFILE_URL))

    assert _FakeSession.requested_urls == urls[:6]
    assert all(response.exited for response in responses[:6])


@pytest.mark.parametrize("status", [201, 404, 500])
def test_fetch_rejects_non_200_statuses(monkeypatch, status):
    _install_response(monkeypatch, _FakeResponse(b"", status=status))

    with pytest.raises(PesRetroStatsError, match="unavailable"):
        asyncio.run(fetch_pes_retro_stats_profile(PROFILE_URL))


@pytest.mark.parametrize(
    "content_type", ["application/json", "text/plain; charset=utf-8", ""]
)
def test_fetch_rejects_wrong_content_types(monkeypatch, content_type):
    _install_response(
        monkeypatch,
        _FakeResponse(
            flight_html(valid_profile_record()), content_type=content_type
        ),
    )

    with pytest.raises(PesRetroStatsError, match="unavailable"):
        asyncio.run(fetch_pes_retro_stats_profile(PROFILE_URL))


@pytest.mark.parametrize("declared_length", ["invalid", -1, (2 * 1024 * 1024) + 1])
def test_fetch_rejects_invalid_or_oversized_content_length(
    monkeypatch, declared_length
):
    _install_response(
        monkeypatch,
        _FakeResponse(
            flight_html(valid_profile_record()), content_length=declared_length
        ),
    )

    with pytest.raises(PesRetroStatsError, match="unavailable"):
        asyncio.run(fetch_pes_retro_stats_profile(PROFILE_URL))


def test_fetch_rejects_streamed_body_over_two_mibibytes(monkeypatch):
    chunk = b"x" * (64 * 1024)
    response = _FakeResponse(chunks=([chunk] * 32) + [b"x"])
    _install_response(monkeypatch, response)

    with pytest.raises(PesRetroStatsError, match="unavailable"):
        asyncio.run(fetch_pes_retro_stats_profile(PROFILE_URL))

    assert response.entered and response.exited


def test_fetch_rejects_invalid_charset(monkeypatch):
    _install_response(
        monkeypatch,
        _FakeResponse(
            flight_html(valid_profile_record()),
            content_type="text/html; charset=not-a-real-charset",
        ),
    )

    with pytest.raises(PesRetroStatsError, match="unavailable"):
        asyncio.run(fetch_pes_retro_stats_profile(PROFILE_URL))


@pytest.mark.parametrize("failure", [asyncio.TimeoutError(), aiohttp.ClientError()])
def test_fetch_normalizes_timeout_and_client_errors(monkeypatch, failure):
    _install_response(monkeypatch, failure)

    with pytest.raises(
        PesRetroStatsError, match="^Pes Retro Stats profile is unavailable$"
    ):
        asyncio.run(fetch_pes_retro_stats_profile(PROFILE_URL))


def test_fetch_revalidates_the_exact_response_url(monkeypatch):
    _install_response(
        monkeypatch,
        _FakeResponse(
            flight_html(valid_profile_record()),
            url="https://evil.example/player/0ce2dbde-marco-palestra",
        ),
    )

    with pytest.raises(PesRetroStatsError, match="unavailable"):
        asyncio.run(fetch_pes_retro_stats_profile(PROFILE_URL))


def test_scraper_package_exports_the_adapter_boundary():
    import scraper

    assert scraper.PesRetroStatsError is PesRetroStatsError
    assert scraper.PesRetroStatsProfile is PesRetroStatsProfile
    assert scraper.fetch_pes_retro_stats_profile is fetch_pes_retro_stats_profile
    assert scraper.parse_pes_retro_stats_profile is parse_pes_retro_stats_profile
    assert scraper.parse_pes_retro_stats_url is parse_pes_retro_stats_url
