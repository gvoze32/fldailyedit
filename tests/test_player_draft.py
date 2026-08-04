import asyncio
from dataclasses import fields

import pytest

from scraper import player_draft
from scraper.player_draft import (
    DraftSourceError,
    PlayerDraftSource,
    fetch_sortitoutsi_player_profile,
    parse_sortitoutsi_person_url,
    parse_sortitoutsi_player_profile,
)
from scraper.sortitoutsi import SORTITOUTSI_HEADERS


PROFILE_URL = (
    "https://sortitoutsi.net/football-manager-data-update/person/2000370206"
)
DASTAN_PROFILE_HTML = """
<!doctype html>
<html>
  <head>
    <link rel="canonical" href="https://sortitoutsi.net/football-manager-data-update/person/2000370206/dastan-satpayev">
    <script type="application/ld+json">
      {
        "@context": "https://schema.org",
        "@type": "Person",
        "@id": "https://sortitoutsi.net/football-manager-data-update/person/2000370206/dastan-satpayev",
        "url": "https://sortitoutsi.net/football-manager-data-update/person/2000370206/dastan-satpayev",
        "name": "  Dastan   Satpayev ",
        "birthDate": "2008-08-12",
        "nationality": {"@type": "Country", "name": "Kazakhstan"},
        "jobTitle": ["AM RL", "ST"],
        "memberOf": {"@type": "SportsTeam", "name": "Chelsea"}
      }
    </script>
  </head>
  <body>
    <h1>Wrong fallback name</h1>
    <dl>
      <dt>Date of Birth</dt><dd>12 August 2008</dd>
      <dt>Current Club</dt><dd>Wrong fallback club</dd>
    </dl>
  </body>
</html>
"""
LABELED_PROFILE_HTML = """
<!doctype html>
<html>
  <head>
    <link rel="canonical" href="https://www.sortitoutsi.net/football-manager-data-update/person/2000370206/dastan-satpayev">
  </head>
  <body>
    <h1>  Dastan   Satpayev </h1>
    <table>
      <tr><th>Date of Birth</th><td>12 August 2008</td></tr>
      <tr><th>Nationality</th><td>Kazakhstan</td></tr>
      <tr><th>Positions</th><td>AM RL; ST</td></tr>
      <tr><th>Current Club</th><td>Chelsea</td></tr>
    </table>
  </body>
</html>
"""
CHALLENGE_HTML = """
<!doctype html>
<html><head><title>Just a moment...</title></head>
<body><div id="cf-chl-widget">Checking your browser before accessing SortitoutSI</div></body></html>
"""
LOGIN_HTML = """
<!doctype html>
<html><head><title>Login | SortitoutSI</title></head>
<body><form action="/login"><input type="password" name="password"></form></body></html>
"""


class _FakeContent:
    def __init__(self, chunks):
        self._chunks = chunks

    async def iter_chunked(self, _size):
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
        history=(),
    ):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.content = _FakeContent(tuple(chunks) if chunks is not None else (data,))
        self.url = url
        self.status = status
        self.history = history
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


class _FakeRedirect:
    def __init__(self, url):
        self.url = url


class _FakeSession:
    response = None
    options = None
    requested_url = None
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
        type(self).requested_url = url
        type(self).allow_redirects = allow_redirects
        return type(self).response


def _install_response(monkeypatch, response):
    _FakeSession.response = response
    _FakeSession.options = None
    _FakeSession.requested_url = None
    _FakeSession.allow_redirects = None
    _FakeSession.last_instance = None
    monkeypatch.setattr(player_draft.aiohttp, "ClientSession", _FakeSession)


def test_parse_person_url_normalizes_allowed_hosts_slug_query_and_fragment():
    assert parse_sortitoutsi_person_url(PROFILE_URL) == (2000370206, PROFILE_URL)
    assert parse_sortitoutsi_person_url(
        "https://www.sortitoutsi.net/football-manager-data-update/person/2000370206/dastan-satpayev?source=community#profile"
    ) == (
        2000370206,
        "https://www.sortitoutsi.net/football-manager-data-update/person/2000370206/dastan-satpayev",
    )


@pytest.mark.parametrize(
    "url",
    (
        "http://sortitoutsi.net/football-manager-data-update/person/2000370206",
        "https://evil.example/football-manager-data-update/person/2000370206",
        "https://players.sortitoutsi.net/football-manager-data-update/person/2000370206",
        "https://sortitoutsi.net.evil.example/football-manager-data-update/person/2000370206",
        "https://user@sortitoutsi.net/football-manager-data-update/person/2000370206",
        "https://sortitoutsi.net:443/football-manager-data-update/person/2000370206",
        "https://sortitoutsi.net/football-manager-data-update/person/not-a-number",
        "https://sortitoutsi.net/football-manager-data-update/person/2000370206/",
        "https://sortitoutsi.net/football-manager-data-update/person/2000370206/Not-Canonical",
        "https://sortitoutsi.net/football-manager-data-update/person/2000370206/dastan-satpayev/extra",
        "https://sortitoutsi.net/football-manager-data-update/person/2000370206%2Fdastan-satpayev",
    ),
)
def test_parse_person_url_rejects_noncanonical_boundaries(url):
    with pytest.raises(DraftSourceError, match="Invalid SortitoutSI person URL"):
        parse_sortitoutsi_person_url(url)


@pytest.mark.parametrize("control", ("\t", "\r", "\n", "\x00", "\x1f", "\x7f"))
def test_parse_person_url_rejects_raw_ascii_control_characters(control):
    url = PROFILE_URL.replace("/football-manager", f"/{control}football-manager")

    with pytest.raises(DraftSourceError, match="Invalid SortitoutSI person URL"):
        parse_sortitoutsi_person_url(url)


@pytest.mark.parametrize("digit_count", (21, 5000))
def test_parse_person_url_rejects_unbounded_numeric_ids(digit_count):
    url = (
        "https://sortitoutsi.net/football-manager-data-update/person/"
        + ("9" * digit_count)
    )

    with pytest.raises(DraftSourceError, match="Invalid SortitoutSI person URL"):
        parse_sortitoutsi_person_url(url)


def test_parse_profile_extracts_structured_source_metadata_before_labeled_html():
    source = parse_sortitoutsi_player_profile(
        DASTAN_PROFILE_HTML,
        PROFILE_URL,
        2000370206,
    )

    assert source == PlayerDraftSource(
        sortitoutsi_id=2000370206,
        name="Dastan Satpayev",
        profile_url=PROFILE_URL,
        date_of_birth="2008-08-12",
        nationality="Kazakhstan",
        positions=("AM RL", "ST"),
        current_club="Chelsea",
    )
    assert tuple(field.name for field in fields(source)) == (
        "sortitoutsi_id",
        "name",
        "profile_url",
        "date_of_birth",
        "nationality",
        "positions",
        "current_club",
    )
    for inferred_field in ("ca", "pa", "abilities", "pes", "rating"):
        assert not hasattr(source, inferred_field)


def test_parse_profile_selects_the_unique_person_with_the_requested_identity():
    html = f"""
    <html><head>
      <link rel="canonical" href="{PROFILE_URL}">
      <script type="application/ld+json">
        {{
          "@graph": [
            {{
              "@type": "Person",
              "@id": "https://sortitoutsi.net/football-manager-data-update/person/111/unrelated",
              "name": "Unrelated Person"
            }},
            {{
              "@type": "Person",
              "url": "{PROFILE_URL}/dastan-satpayev",
              "name": "Dastan Satpayev",
              "birthDate": "2008-08-12"
            }}
          ]
        }}
      </script>
    </head><body></body></html>
    """

    source = parse_sortitoutsi_player_profile(html, PROFILE_URL, 2000370206)

    assert source.name == "Dastan Satpayev"
    assert source.date_of_birth == "2008-08-12"


def test_parse_profile_ignores_nonmatching_people_when_page_identity_matches():
    html = f"""
    <html><head>
      <link rel="canonical" href="{PROFILE_URL}">
      <script type="application/ld+json">
        {{
          "@graph": [
            {{
              "@type": "Person",
              "@id": "https://sortitoutsi.net/football-manager-data-update/person/111/author",
              "name": "Profile Author"
            }},
            {{"@type": "Person", "name": "Unidentified Contributor"}}
          ]
        }}
      </script>
    </head><body>
      <h1>Dastan Satpayev</h1>
      <dl>
        <dt>Date of Birth</dt><dd>12 August 2008</dd>
        <dt>Nationality</dt><dd>Kazakhstan</dd>
        <dt>Positions</dt><dd>AM RL; ST</dd>
        <dt>Current Club</dt><dd>Chelsea</dd>
      </dl>
    </body></html>
    """

    source = parse_sortitoutsi_player_profile(html, PROFILE_URL, 2000370206)

    assert source.name == "Dastan Satpayev"
    assert source.date_of_birth == "12 August 2008"
    assert source.positions == ("AM RL", "ST")
    assert source.current_club == "Chelsea"


def test_parse_profile_rejects_ambiguous_people_with_the_requested_identity():
    html = f"""
    <html><head>
      <link rel="canonical" href="{PROFILE_URL}">
      <script type="application/ld+json">
        {{
          "@graph": [
            {{"@type": "Person", "@id": "{PROFILE_URL}", "name": "First Person"}},
            {{"@type": "Person", "url": "{PROFILE_URL}/second", "name": "Second Person"}}
          ]
        }}
      </script>
    </head><body></body></html>
    """

    with pytest.raises(DraftSourceError, match="^SortitoutSI profile is unavailable$"):
        parse_sortitoutsi_player_profile(html, PROFILE_URL, 2000370206)


def test_parse_profile_normalizes_recursive_json_ld_failure():
    recursive_json = '{"@graph":' * 1200 + "[]" + "}" * 1200
    html = (
        f'<html><head><link rel="canonical" href="{PROFILE_URL}">'
        f'<script type="application/ld+json">{recursive_json}</script>'
        "</head><body><h1>Dastan Satpayev</h1></body></html>"
    )

    with pytest.raises(DraftSourceError, match="^SortitoutSI profile is unavailable$"):
        parse_sortitoutsi_player_profile(html, PROFILE_URL, 2000370206)


def test_parse_profile_falls_back_to_labeled_html_and_retains_source_text():
    source = parse_sortitoutsi_player_profile(
        LABELED_PROFILE_HTML,
        PROFILE_URL,
        2000370206,
    )

    assert source.name == "Dastan Satpayev"
    assert source.date_of_birth == "12 August 2008"
    assert source.nationality == "Kazakhstan"
    assert source.positions == ("AM RL", "ST")
    assert source.current_club == "Chelsea"


@pytest.mark.parametrize("html", (CHALLENGE_HTML, LOGIN_HTML))
def test_parse_profile_rejects_challenge_and_login_pages_deterministically(html):
    with pytest.raises(
        DraftSourceError,
        match="^SortitoutSI profile is unavailable$",
    ):
        parse_sortitoutsi_player_profile(html, PROFILE_URL, 2000370206)


@pytest.mark.parametrize(
    "html",
    (
        '<html><head><link rel="canonical" href="' + PROFILE_URL + '"></head><body></body></html>',
        "<html><body><h1>Dastan Satpayev</h1></body></html>",
    ),
)
def test_parse_profile_rejects_missing_name_or_page_identity(html):
    with pytest.raises(DraftSourceError, match="^SortitoutSI profile is unavailable$"):
        parse_sortitoutsi_player_profile(html, PROFILE_URL, 2000370206)


def test_parse_profile_rejects_mismatched_person_id():
    mismatch = DASTAN_PROFILE_HTML.replace("2000370206", "123456789")

    with pytest.raises(DraftSourceError, match="^SortitoutSI profile is unavailable$"):
        parse_sortitoutsi_player_profile(mismatch, PROFILE_URL, 2000370206)


def test_fetch_uses_existing_headers_timeout_and_allowed_canonical_redirect(monkeypatch):
    redirected_url = PROFILE_URL + "/dastan-satpayev"
    response = _FakeResponse(
        DASTAN_PROFILE_HTML,
        url=redirected_url,
        history=(
            _FakeRedirect(PROFILE_URL),
            _FakeRedirect(
                "https://www.sortitoutsi.net/football-manager-data-update/"
                "person/2000370206/dastan-satpayev"
            ),
        ),
    )
    _install_response(monkeypatch, response)

    source = asyncio.run(fetch_sortitoutsi_player_profile(PROFILE_URL + "?from=test"))

    assert source.profile_url == redirected_url
    assert source.sortitoutsi_id == 2000370206
    assert _FakeSession.requested_url == PROFILE_URL
    assert _FakeSession.allow_redirects is True
    assert _FakeSession.options["headers"] == SORTITOUTSI_HEADERS
    assert _FakeSession.options["timeout"].total == 30
    assert response.entered and response.exited
    assert _FakeSession.last_instance.entered and _FakeSession.last_instance.exited


@pytest.mark.parametrize(
    "response",
    (
        _FakeResponse(DASTAN_PROFILE_HTML, content_type="application/json"),
        _FakeResponse(DASTAN_PROFILE_HTML, status=503),
    ),
)
def test_fetch_rejects_non_html_and_http_error(monkeypatch, response):
    _install_response(monkeypatch, response)

    with pytest.raises(DraftSourceError, match="^SortitoutSI profile is unavailable$"):
        asyncio.run(fetch_sortitoutsi_player_profile(PROFILE_URL))


def test_fetch_rejects_off_host_intermediate_redirect_with_allowed_final(monkeypatch):
    response = _FakeResponse(
        DASTAN_PROFILE_HTML,
        url=PROFILE_URL + "/dastan-satpayev",
        history=(
            _FakeRedirect(PROFILE_URL),
            _FakeRedirect(
                "https://evil.example/football-manager-data-update/person/2000370206"
            ),
        ),
    )
    _install_response(monkeypatch, response)

    with pytest.raises(DraftSourceError, match="^SortitoutSI profile is unavailable$"):
        asyncio.run(fetch_sortitoutsi_player_profile(PROFILE_URL))

    assert response.entered and response.exited
    assert _FakeSession.last_instance.entered and _FakeSession.last_instance.exited


def test_fetch_rejects_response_body_above_two_mibibytes(monkeypatch):
    small_chunk = b"x" * (64 * 1024)
    response = _FakeResponse(chunks=([small_chunk] * 32) + [b"x"])
    _install_response(monkeypatch, response)

    with pytest.raises(DraftSourceError, match="^SortitoutSI profile is unavailable$"):
        asyncio.run(fetch_sortitoutsi_player_profile(PROFILE_URL))

    assert response.entered and response.exited
    assert _FakeSession.last_instance.entered and _FakeSession.last_instance.exited


def test_fetch_rejects_declared_response_body_above_two_mibibytes(monkeypatch):
    _install_response(
        monkeypatch,
        _FakeResponse(
            DASTAN_PROFILE_HTML,
            content_length=(2 * 1024 * 1024) + 1,
        ),
    )

    with pytest.raises(DraftSourceError, match="^SortitoutSI profile is unavailable$"):
        asyncio.run(fetch_sortitoutsi_player_profile(PROFILE_URL))
