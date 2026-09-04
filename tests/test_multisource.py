"""Fixtures and safety rules for supplemental transfer sources."""

import asyncio
from datetime import date
import logging
from types import SimpleNamespace

import pytest

from run import _match_transfers_statefully
from scraper.matcher import NameMatcher
from scraper.models import Transfer
from scraper.sortitoutsi import parse_sortitoutsi_markdown
from scraper.sources import reconcile_transfer_sources
from scraper.wikipedia import (
    parse_wikipedia_transfer_html,
    parse_wikipedia_transfer_wikitext,
)


WIKIPEDIA_HTML = """
<table class="wikitable sortable"><tbody>
<tr><th>Date</th><th>Player</th><th>Moving from</th><th>Moving to</th><th>Fee</th></tr>
<tr><td rowspan="2">3 August 2026</td><td>Jordan Henderson</td>
<td>Brentford</td><td>Chelsea</td><td><span style="display:none">sort</span>Free<sup>[1]</sup></td></tr>
<tr><td>Loan Player</td><td>Parent FC</td><td>Loan FC</td><td>Loan</td></tr>
<tr><td>4 August 2026</td><td>Future Player</td><td>A</td><td>B</td><td>Free</td></tr>
</tbody></table>
"""

WIKIPEDIA_WIKITEXT = """
{| class="wikitable sortable"
!Date
!Player
!Moving from
!Moving to
!Fee
|-
|rowspan="2"|3 August 2026
|{{flagg|cxx|ENG}} {{sortname|Jordan|Henderson}}
|[[Brentford F.C.|Brentford]]
|[[Chelsea F.C.|Chelsea]]
|{{ntsh|0}}Free<ref>{{cite web|url=https://www.chelseafc.com/proof|title=Proof}}</ref>
|-
|{{sortname|Loan|Player}}
|[[Parent F.C.|Parent FC]]
|[[Loan F.C.|Loan FC]]
|{{Loan}}
|}
"""


WIKIPEDIA_DTS_WIKITEXT = """
{|class="wikitable sortable"
|-
! Date
! Name
! Moving from
! Moving to
! Fee
|-
|'''{{dts|format=dmy|2026|8|3}}'''
|{{Sort|Jiménez, Álex|{{flagicon|ESP}} [[Álex Jiménez (footballer, born 2005)|Álex Jiménez]]}}
|''[[AC Milan|Milan]]''
|{{flagicon|ENG}} [[AFC Bournemouth|Bournemouth]]
|Loan<ref>{{cite web|url=https://afcb.example/jimenez|title=Proof}}</ref>
|}
"""


WIKIPEDIA_A_LEAGUE_WIKITEXT = """
{| class="wikitable sortable"
|-
! Date
! Name
! Moving from
! Moving to
|-
| {{dts|format=dmy|2026|6|5}} || {{sortname|Kaelan|Majekodunmi}} || [[Dandenong Thunder FC|Dandenong Thunder]] || {{A-League team|PG}} (end of loan)<ref>{{cite web|url=https://perthglory.example/majekodunmi|title=Proof}}</ref>
|}
"""



WIKIPEDIA_CLUB_WIKITEXT = """
==Bundesliga==
===Bayern Munich===
'''In:'''
{{fs start|hidenote=yes}}
{{fs player|no=11|nat=GER|pos=DF|name=[[Nathaniel Brown (footballer)|Nathaniel Brown]]|other=from [[Eintracht Frankfurt]]}}<ref>{{cite web|url=https://fcbayern.example/brown|title=Proof}}</ref>
{{Fs end}}
'''Out:'''
{{fs start|hidenote=yes}}
{{fs player|no=|nat=ISR|pos=GK|name=[[Daniel Peretz]]|other=on loan to [[Southampton F.C.|Southampton]]}}<ref>{{cite web|url=https://fcbayern.example/peretz|title=Proof}}</ref>
{{Fs end}}
"""

SORTITOUTSI_MARKDOWN = """
## [Recent Submissions](https://sortitoutsi.net/football-manager-data-update/submissions)

 Enabled
##### [Jordan Henderson](https://sortitoutsi.net/football-manager-data-update/person/28005568)
[03 Aug 2026 15:20:54](https://sortitoutsi.net/football-manager-data-update/submission/1246519)
[Jordan Henderson](https://sortitoutsi.net/football-manager-data-update/person/28005568 "Jordan") has been transferred to [Chelsea](https://sortitoutsi.net/football-manager-data-update/team/630 "Chelsea").
[Jordan Henderson](https://sortitoutsi.net/football-manager-data-update/person/28005568) now has a contract starting on 3rd August 2026.
[Proof (chelseafc.com)](https://www.chelseafc.com/jordan-henderson-joins)
Last Moderated by Moderator 03 Aug 2026 15:37:09

 Pending
##### [Pending Player](https://sortitoutsi.net/football-manager-data-update/person/99)
[03 Aug 2026 15:21:55](https://sortitoutsi.net/football-manager-data-update/submission/2)
[Pending Player](https://sortitoutsi.net/football-manager-data-update/person/99) is now on loan to [Loan FC](https://sortitoutsi.net/football-manager-data-update/team/10) starting on 3rd August 2026.
[Proof (club.example)](https://club.example/proof)

 Enabled
##### [No Proof](https://sortitoutsi.net/football-manager-data-update/person/100)
[03 Aug 2026 15:22:55](https://sortitoutsi.net/football-manager-data-update/submission/3)
[No Proof](https://sortitoutsi.net/football-manager-data-update/person/100) has been transferred to [Club](https://sortitoutsi.net/football-manager-data-update/team/11).
"""


SORTITOUTSI_SUBMISSION_ONLY_MARKDOWN = """
## [Recent Submissions](https://sortitoutsi.net/football-manager-data-update/submissions)

 Enabled
##### [Fast Player](https://sortitoutsi.net/football-manager-data-update/person/200)
[03 Aug 2026 15:20:54](https://sortitoutsi.net/football-manager-data-update/submission/20)
[Fast Player](https://sortitoutsi.net/football-manager-data-update/person/200) has been transferred to [Chelsea](https://sortitoutsi.net/football-manager-data-update/team/630 "Chelsea").
[Proof (chelseafc.com)](https://www.chelseafc.com/fast-player-joins)
"""


TRANSFERMARKT_MARKDOWN = """
| Player | Age | Nat. | Left | Joined | Transfer date | Market value | Fee |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ![João Mário](https://img.example/537602.jpg)[João Mário](https://www.transfermarkt.com/joao-mario/profil/spieler/537602 "João Mário") Right-Back | 26 | Portugal | [Juventus](https://www.transfermarkt.com/juventus-turin/startseite/verein/506/saison_id/2026 "Juventus FC") | [Fiorentina](https://www.transfermarkt.com/ac-florenz/startseite/verein/430/saison_id/2026 "ACF Fiorentina") | 03/08/2026 | €18.00m | [Loan fee: €1.80m](https://www.transfermarkt.com/jumplist/transfers/spieler/537602/transfer_id/6481933) |
| [Péter Gulácsi](https://www.transfermarkt.com/peter-gulacsi/profil/spieler/57071 "Péter Gulácsi") Goalkeeper | 36 | Hungary | [Leipzig](https://www.transfermarkt.com/rasenballsport-leipzig/startseite/verein/23826/saison_id/2026 "RB Leipzig") | [Villarreal](https://www.transfermarkt.com/fc-villarreal/startseite/verein/1050/saison_id/2026 "Villarreal CF") | 03/08/2026 | €1.00m | [€2.00m](https://www.transfermarkt.com/jumplist/transfers/spieler/57071/transfer_id/6481832) |
| [Jordan Henderson](https://www.transfermarkt.com/jordan-henderson/profil/spieler/61651 "Jordan Henderson") Defensive Midfield | 36 | England | [Brentford](https://www.transfermarkt.com/fc-brentford/startseite/verein/1148/saison_id/2026 "Brentford FC") | [Chelsea](https://www.transfermarkt.com/fc-chelsea/startseite/verein/631/saison_id/2026 "Chelsea FC") | 03/08/2026 | €3.50m | [free transfer](https://www.transfermarkt.com/jumplist/transfers/spieler/61651/transfer_id/6481567) |
"""


TRANSFERMARKT_PAGE_2 = """
| Player | Age | Nat. | Left | Joined | Transfer date | Market value | Fee |
| --- | --- | --- | --- | --- | --- | --- | --- |
| [Shawn Adewoye](https://www.transfermarkt.com/shawn-adewoye/profil/spieler/358021 "Shawn Adewoye") Centre-Back | 26 | Belgium | [Fortuna Sittard](https://www.transfermarkt.com/fortuna-sittard/startseite/verein/385/saison_id/2026 "Fortuna Sittard") | [Lommel SK](https://www.transfermarkt.com/lommel-sk/startseite/verein/5026/saison_id/2026 "Lommel SK") | 02/08/2026 | €600k | [free transfer](https://www.transfermarkt.com/jumplist/transfers/spieler/358021/transfer_id/6481688) |
"""


TRANSFERMARKT_LOCALIZED_MARKDOWN = """
| Spieler | Alter | Nat. | Abgebender Verein | Aufnehmender Verein | Transferdatum | Marktwert | Ablöse |
| --- | --- | --- | --- | --- | --- | --- | --- |
| [Max Mustermann](https://www.transfermarkt.de/max-mustermann/profil/spieler/123 "Max Mustermann") Innenverteidiger | 24 |  | [Old FC](https://www.transfermarkt.de/old-fc/startseite/verein/10/saison_id/2026 "Old FC") | [New FC](https://www.transfermarkt.de/new-fc/startseite/verein/20/saison_id/2026 "New FC") | 04.09.2026 | €1,20m | [Leihgebühr](https://www.transfermarkt.de/jumplist/transfers/spieler/123/transfer_id/999 "Leihgebühr") |
"""


def test_wikipedia_parser_handles_rowspan_types_and_effective_date_filter():
    transfers = parse_wikipedia_transfer_html(
        WIKIPEDIA_HTML,
        "List of English football transfers summer 2026",
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 3),
    )

    assert [(item.player_name, item.transfer_type) for item in transfers] == [
        ("Jordan Henderson", "free transfer"),
        ("Loan Player", "loan"),
    ]
    assert transfers[0].fee == "Free"
    assert transfers[0].sources == ("wikipedia",)


def test_wikipedia_bulk_wikitext_parser_preserves_route_and_proof():
    transfers = parse_wikipedia_transfer_wikitext(
        WIKIPEDIA_WIKITEXT,
        "List of English football transfers summer 2026",
        end_date=date(2026, 8, 3),
    )

    assert [(item.player_name, item.transfer_type) for item in transfers] == [
        ("Jordan Henderson", "free transfer"),
        ("Loan Player", "loan"),
    ]
    assert transfers[0].proof_urls == ("https://www.chelseafc.com/proof",)


def test_wikipedia_wikitext_parser_accepts_leading_row_and_dts_date():
    transfers = parse_wikipedia_transfer_wikitext(
        WIKIPEDIA_DTS_WIKITEXT,
        "List of Italian football transfers summer 2026",
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 3),
    )

    assert len(transfers) == 1
    transfer = transfers[0]
    assert (
        transfer.player_name,
        transfer.from_club,
        transfer.to_club,
        transfer.date,
        transfer.transfer_type,
    ) == (
        "Álex Jiménez",
        "Milan",
        "Bournemouth",
        "2026-08-03",
        "loan",
    )
    assert transfer.proof_urls == ("https://afcb.example/jimenez",)


def test_wikipedia_a_league_template_expands_team_name():
    transfers = parse_wikipedia_transfer_wikitext(
        WIKIPEDIA_A_LEAGUE_WIKITEXT,
        "A-League Men transfers for 2026–27 season",
    )

    assert len(transfers) == 1
    assert transfers[0].to_club == "Perth Glory"


def test_wikipedia_a_league_annotation_sets_lifecycle_type():
    transfers = parse_wikipedia_transfer_wikitext(
        WIKIPEDIA_A_LEAGUE_WIKITEXT,
        "A-League Men transfers for 2026–27 season",
    )

    assert len(transfers) == 1
    assert transfers[0].transfer_type == "end of loan"
    assert transfers[0].is_loan is False
    assert transfers[0].proof_urls == (
        "https://perthglory.example/majekodunmi",
    )




def test_wikipedia_club_list_parser_emits_undated_route_corroborators():
    transfers = parse_wikipedia_transfer_wikitext(
        WIKIPEDIA_CLUB_WIKITEXT,
        "List of German football transfers summer 2026",
    )

    assert [
        (
            item.player_name,
            item.from_club,
            item.to_club,
            item.transfer_type,
            item.position,
        )
        for item in transfers
    ] == [
        (
            "Nathaniel Brown",
            "Eintracht Frankfurt",
            "Bayern Munich",
            "transfer",
            "DF",
        ),
        (
            "Daniel Peretz",
            "Bayern Munich",
            "Southampton",
            "loan",
            "GK",
        ),
    ]
    assert all(item.date == "" for item in transfers)
    assert all(item.verification_status == "corroborator" for item in transfers)
    assert all(item.infer_from_current_roster is False for item in transfers)
    assert transfers[0].proof_urls == ("https://fcbayern.example/brown",)
    assert transfers[1].is_loan is True

def test_wikipedia_fetch_discovers_every_mens_page_in_season_category(monkeypatch):
    from scraper import wikipedia

    requested = []

    async def fake_fetch(_session, **params):
        requested.append(params)
        if params.get("list") == "categorymembers":
            return {
                "query": {
                    "categorymembers": [
                        {"title": "List of English football transfers summer 2026"},
                        {"title": "List of Italian football transfers summer 2026"},
                        {"title": "A-League Men transfers for 2026–27 season"},
                        {
                            "title": (
                                "List of English women's football transfers "
                                "summer 2026"
                            )
                        },
                    ]
                }
            }
        return {"query": {"pages": {}}}

    monkeypatch.setattr(wikipedia, "_fetch_json", fake_fetch)

    transfers = asyncio.run(
        wikipedia._fetch_wikipedia_transfers_async(
            window="summer",
            ref_date=date(2026, 8, 4),
        )
    )

    assert transfers == []
    assert any(call.get("list") == "categorymembers" for call in requested)
    titles = next(
        call["titles"] for call in requested if call.get("prop") == "revisions"
    )
    assert set(titles.split("|")) == {
        "List of English football transfers summer 2026",
        "List of Italian football transfers summer 2026",
        "A-League Men transfers for 2026–27 season",
    }


def test_wikipedia_fetch_does_not_replay_wikimedia_rate_limit_cookies(monkeypatch):
    from scraper import wikipedia

    created_cookie_jars = []
    real_client_session = wikipedia.aiohttp.ClientSession

    def recording_client_session(*args, **kwargs):
        created_cookie_jars.append(kwargs.get("cookie_jar"))
        return real_client_session(*args, **kwargs)

    async def fake_fetch(_session, **_params):
        return {"query": {"categorymembers": []}}

    monkeypatch.setattr(wikipedia.aiohttp, "ClientSession", recording_client_session)
    monkeypatch.setattr(wikipedia, "_fetch_json", fake_fetch)

    transfers = asyncio.run(
        wikipedia._fetch_wikipedia_transfers_async(
            window="summer",
            ref_date=date(2026, 8, 4),
        )
    )

    assert transfers == []
    assert len(created_cookie_jars) == 1
    assert isinstance(created_cookie_jars[0], wikipedia.aiohttp.DummyCookieJar)

def test_sortitoutsi_parser_accepts_only_enabled_entries_with_proof():
    transfers = parse_sortitoutsi_markdown(
        SORTITOUTSI_MARKDOWN,
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 3),
    )

    assert len(transfers) == 1
    transfer = transfers[0]
    assert transfer.player_name == "Jordan Henderson"
    assert transfer.to_club == "Chelsea"
    assert transfer.from_club == ""
    assert transfer.verification_status == "enabled"
    assert transfer.infer_from_current_roster is True
    assert transfer.proof_urls == ("https://www.chelseafc.com/jordan-henderson-joins",)


def test_sortitoutsi_submission_date_only_disables_roster_inference():
    transfers = parse_sortitoutsi_markdown(
        SORTITOUTSI_SUBMISSION_ONLY_MARKDOWN,
        end_date=date(2026, 8, 3),
    )

    assert len(transfers) == 1
    assert transfers[0].date == "2026-08-03"
    assert transfers[0].verification_status == "enabled"
    assert transfers[0].infer_from_current_roster is False


def test_sortitoutsi_submission_date_only_cannot_create_event():
    signals = parse_sortitoutsi_markdown(
        SORTITOUTSI_SUBMISSION_ONLY_MARKDOWN,
        end_date=date(2026, 8, 3),
    )

    assert reconcile_transfer_sources([], signals) == []


def test_transfermarkt_detailed_parser_extracts_dated_verified_events():
    try:
        from scraper.transfermarkt import parse_transfermarkt_markdown
    except ModuleNotFoundError:
        pytest.fail("Transfermarkt parser is not implemented")

    transfers = parse_transfermarkt_markdown(
        TRANSFERMARKT_MARKDOWN,
        "https://www.transfermarkt.com/transfers/neuestetransfers/statistik?plus=1",
        start_date=date(2026, 8, 3),
        end_date=date(2026, 8, 3),
    )

    assert [item.player_name for item in transfers] == [
        "João Mário",
        "Péter Gulácsi",
        "Jordan Henderson",
    ]
    joao, peter, jordan = transfers
    assert (
        joao.from_club,
        joao.to_club,
        joao.date,
        joao.transfer_type,
        joao.is_loan,
        joao.market_value,
        joao.verification_status,
    ) == (
        "Juventus FC",
        "ACF Fiorentina",
        "2026-08-03",
        "loan",
        True,
        18_000_000,
        "verified",
    )
    assert (
        joao.player_id_transfermarkt,
        joao.from_club_id_transfermarkt,
        joao.to_club_id_transfermarkt,
        joao.transfer_id_transfermarkt,
    ) == (537602, 506, 430, 6481933)
    assert joao.sources == ("transfermarkt",)
    assert joao.infer_from_current_roster is False
    assert peter.transfer_type == "transfer"
    assert jordan.transfer_type == "free transfer"
    assert jordan.source_urls[-1].endswith("/transfer_id/6481567")


def test_transfermarkt_parser_accepts_localized_columns_and_urls():
    from scraper.transfermarkt import parse_transfermarkt_markdown

    transfers = parse_transfermarkt_markdown(
        TRANSFERMARKT_LOCALIZED_MARKDOWN,
        "https://www.transfermarkt.de/transfers/neuestetransfers/statistik",
        start_date=date(2026, 9, 4),
        end_date=date(2026, 9, 4),
    )

    assert len(transfers) == 1
    transfer = transfers[0]
    assert (
        transfer.player_name,
        transfer.from_club,
        transfer.to_club,
        transfer.date,
        transfer.position,
        transfer.market_value,
        transfer.player_id_transfermarkt,
        transfer.transfer_id_transfermarkt,
    ) == (
        "Max Mustermann",
        "Old FC",
        "New FC",
        "2026-09-04",
        "Innenverteidiger",
        1_200_000,
        123,
        999,
    )
    assert (transfer.transfer_type, transfer.is_loan) == ("loan", True)




def test_transfermarkt_parser_accepts_current_reader_table_shape():
    from scraper import transfermarkt

    markdown = """
| Player | Age | Nat. | Left | Joined | [Transfer date](https://www.transfermarkt.com/transfers/neuestetransfers/statistik?sort=datum_hidden.desc) | [Market value](https://www.transfermarkt.com/transfers/neuestetransfers/statistik?sort=marktwert.desc) | [Fee](https://www.transfermarkt.com/transfers/neuestetransfers/statistik?sort=abloese.desc) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ![Image 1: Current Player](https://img.transfermarkt.test/player.png)[Current Player](https://www.transfermarkt.com/current-player/profil/spieler/123 "Current Player") Central Midfield | 25 | ![Image 2: Country](https://img.transfermarkt.test/country.png) | [![Image 3: Old FC](https://img.transfermarkt.test/old.png)](https://www.transfermarkt.com/old-fc/startseite/verein/10/saison_id/2026)[Old FC](https://www.transfermarkt.com/old-fc/startseite/verein/10/saison_id/2026 "Old FC") | [![Image 4: New FC](https://img.transfermarkt.test/new.png)](https://www.transfermarkt.com/new-fc/startseite/verein/20/saison_id/2026)[New FC](https://www.transfermarkt.com/new-fc/startseite/verein/20/saison_id/2026 "New FC") | 05/09/2026 | €1.20m | [?](https://www.transfermarkt.com/jumplist/transfers/spieler/123/transfer_id/1001) |
"""

    transfers = transfermarkt.parse_transfermarkt_markdown(
        markdown,
        "https://www.transfermarkt.com/transfers/neuestetransfers/statistik",
        start_date=date(2026, 9, 5),
        end_date=date(2026, 9, 5),
    )

    assert len(transfers) == 1
    assert (
        transfers[0].player_name,
        transfers[0].from_club,
        transfers[0].to_club,
        transfers[0].position,
        transfers[0].market_value,
    ) == ("Current Player", "Old FC", "New FC", "Central Midfield", 1_200_000)

def test_transfermarkt_fetch_uses_detailed_pages_and_stops_below_cutoff(monkeypatch):
    from scraper import transfermarkt

    requested = []

    async def fake_fetch(_session, reader_url):
        requested.append(reader_url)
        return TRANSFERMARKT_PAGE_2 if "page=2" in reader_url else TRANSFERMARKT_MARKDOWN

    monkeypatch.setattr(transfermarkt, "_fetch_text", fake_fetch)
    transfers = asyncio.run(
        transfermarkt._fetch_transfermarkt_transfers_async(
            max_pages=4,
            since_date=date(2026, 8, 3),
            ref_date=date(2026, 8, 4),
        )
    )

    assert [item.transfer_id_transfermarkt for item in transfers] == [
        6481933,
        6481832,
        6481567,
    ]
    base_url = (
        "https://r.jina.ai/https://www.transfermarkt.com/transfers/"
        "neuestetransfers/statistik?"
        "land_id=0&verein_land_id=0&wettbewerb_id=alle&plus=1"
    )
    assert requested == [base_url, f"{base_url}&page=2"]
    assert transfers[0].source_urls[0].endswith(
        "wettbewerb_id=alle&plus=1"
    )



def test_transfermarkt_fetch_automatically_continues_past_four_pages(monkeypatch):
    from scraper import transfermarkt

    page_markdowns = {}
    source_ids = ("6481933", "6481832", "6481567")
    for page in range(1, 6):
        markdown = TRANSFERMARKT_MARKDOWN
        for index, source_id in enumerate(source_ids, start=1):
            markdown = markdown.replace(
                source_id,
                str(7_000_000 + page * 10 + index),
            )
        page_markdowns[page] = markdown
    old_page = TRANSFERMARKT_MARKDOWN.replace("03/08/2026", "01/08/2026")
    requested = []

    async def fake_fetch(_session, reader_url):
        requested.append(reader_url)
        if "page=" not in reader_url:
            return page_markdowns[1]
        page = int(reader_url.split("page=", 1)[1].split("&", 1)[0])
        return page_markdowns.get(page, old_page)

    monkeypatch.setattr(transfermarkt, "_fetch_text", fake_fetch)
    transfers = asyncio.run(
        transfermarkt._fetch_transfermarkt_transfers_async(
            since_date=date(2026, 8, 3),
            ref_date=date(2026, 8, 4),
        )
    )

    assert len(transfers) == 15
    assert len(requested) == 6
    assert "page=5" in requested[-2]


def test_transfermarkt_fetch_has_no_250_transfer_cap(monkeypatch, caplog):
    from scraper import transfermarkt

    source_ids = ("6481933", "6481832", "6481567")
    page_markdowns = {}
    for page in range(1, 85):
        markdown = TRANSFERMARKT_MARKDOWN
        for index, source_id in enumerate(source_ids, start=1):
            markdown = markdown.replace(
                source_id,
                str(8_000_000 + page * 10 + index),
            )
        page_markdowns[page] = markdown

    async def fake_fetch(_session, reader_url):
        if "page=" not in reader_url:
            return page_markdowns[1]
        page = int(reader_url.split("page=", 1)[1].split("&", 1)[0])
        return page_markdowns.get(page, page_markdowns[84])

    monkeypatch.setattr(transfermarkt, "_fetch_text", fake_fetch)
    with caplog.at_level(logging.INFO, logger=transfermarkt.__name__):
        transfers = asyncio.run(
            transfermarkt._fetch_transfermarkt_transfers_async(
                max_pages=85,
                since_date=date(2026, 8, 3),
                ref_date=date(2026, 8, 4),
            )
        )

    assert len(transfers) == 252
    assert len({item.transfer_id_transfermarkt for item in transfers}) == 252
    assert any(
        "latest-feed boundary reached after 84 unique pages" in record.message
        for record in caplog.records
    )


def test_transfermarkt_fetch_continues_past_future_dated_first_page(monkeypatch):
    from scraper import transfermarkt

    requested = []
    future_page = TRANSFERMARKT_MARKDOWN.replace("03/08/2026", "04/08/2026")
    valid_page = TRANSFERMARKT_PAGE_2.replace("02/08/2026", "03/08/2026")

    async def fake_fetch(_session, reader_url):
        requested.append(reader_url)
        return valid_page if "page=2" in reader_url else future_page

    monkeypatch.setattr(transfermarkt, "_fetch_text", fake_fetch)
    transfers = asyncio.run(
        transfermarkt._fetch_transfermarkt_transfers_async(
            max_pages=2,
            since_date=date(2026, 8, 3),
            ref_date=date(2026, 8, 3),
        )
    )

    assert [item.transfer_id_transfermarkt for item in transfers] == [6481688]
    assert len(requested) == 2


def test_transfermarkt_fetch_refreshes_empty_reader_response(monkeypatch):
    from scraper import transfermarkt

    requested = []

    async def fake_fetch(_session, reader_url):
        requested.append(reader_url)
        return (
            TRANSFERMARKT_MARKDOWN
            if "fldailyedit_refresh=" in reader_url
            else ""
        )

    monkeypatch.setattr(transfermarkt, "_fetch_text", fake_fetch)
    transfers = asyncio.run(
        transfermarkt._fetch_transfermarkt_transfers_async(
            max_pages=1,
            since_date=date(2026, 8, 3),
            ref_date=date(2026, 8, 3),
        )
    )

    assert len(transfers) == 3
    assert len(requested) == 2
    assert "fldailyedit_refresh=" in requested[1]


def test_transfermarkt_fetch_falls_back_to_german_reader_domain(monkeypatch):
    from scraper import transfermarkt

    requested = []

    async def fake_fetch(_session, reader_url):
        requested.append(reader_url)
        if "transfermarkt.de" in reader_url:
            return TRANSFERMARKT_LOCALIZED_MARKDOWN
        return ""

    monkeypatch.setattr(transfermarkt, "_fetch_text", fake_fetch)
    transfers = asyncio.run(
        transfermarkt._fetch_transfermarkt_transfers_async(
            max_pages=1,
            since_date=date(2026, 9, 4),
            ref_date=date(2026, 9, 4),
        )
    )

    assert len(transfers) == 1
    assert transfers[0].source_urls[0].startswith(
        "https://www.transfermarkt.de/"
    )
    assert len(requested) == 3
    assert "transfermarkt.com" in requested[0]
    assert "fldailyedit_refresh=" in requested[1]
    assert "transfermarkt.de" in requested[2]


def test_transfermarkt_network_outage_is_quiet_fallback(monkeypatch, caplog):
    from scraper import transfermarkt

    async def fail_fetch(*args, **kwargs):
        raise TimeoutError()

    monkeypatch.setattr(
        transfermarkt,
        "_fetch_transfermarkt_transfers_async",
        fail_fetch,
    )

    with caplog.at_level(logging.DEBUG, logger=transfermarkt.__name__):
        assert transfermarkt.fetch_transfermarkt_transfers() == []

    assert any(
        record.levelno == logging.DEBUG
        and "supplemental source unavailable" in record.message
        for record in caplog.records
    )
    assert not any(record.levelno >= logging.WARNING for record in caplog.records)


def test_reconciliation_enriches_complete_route_with_fast_signal():
    wikipedia = parse_wikipedia_transfer_html(
        WIKIPEDIA_HTML,
        "List of English football transfers summer 2026",
        end_date=date(2026, 8, 3),
    )
    signal = parse_sortitoutsi_markdown(
        SORTITOUTSI_MARKDOWN, end_date=date(2026, 8, 3)
    )

    reconciled = reconcile_transfer_sources([wikipedia], signal)
    jordan = next(item for item in reconciled if item.player_name == "Jordan Henderson")

    assert (jordan.from_club, jordan.to_club) == ("Brentford", "Chelsea")
    assert jordan.sources == ("wikipedia", "sortitoutsi")
    assert jordan.player_id_sortitoutsi == 28005568
    assert jordan.infer_from_current_roster is False


def test_transfermarkt_dated_routes_merge_with_matching_wikipedia_event():
    from scraper.transfermarkt import parse_transfermarkt_markdown

    wikipedia = parse_wikipedia_transfer_html(
        WIKIPEDIA_HTML,
        "List of English football transfers summer 2026",
        end_date=date(2026, 8, 3),
    )
    transfermarkt = parse_transfermarkt_markdown(
        TRANSFERMARKT_MARKDOWN,
        "https://www.transfermarkt.com/transfers/neuestetransfers/statistik?plus=1",
    )

    reconciled = reconcile_transfer_sources([wikipedia, transfermarkt])

    assert [item.player_name for item in reconciled] == [
        "Jordan Henderson",
        "Loan Player",
        "João Mário",
        "Péter Gulácsi",
    ]
    jordan = reconciled[0]
    assert jordan.date == "2026-08-03"
    assert jordan.sources == ("wikipedia", "transfermarkt")
    assert jordan.player_id_transfermarkt == 61651
    assert jordan.transfer_id_transfermarkt == 6481567
    assert jordan.infer_from_current_roster is False


def test_free_transfer_merges_free_agent_and_previous_club_routes():
    fotmob = Transfer(
        "Jordan Henderson",
        "Free Agent",
        "Chelsea",
        date="2026-08-03T17:16:20Z",
        transfer_type="free transfer",
    )
    transfermarkt = Transfer(
        "Jordan Henderson",
        "Brentford FC",
        "Chelsea FC",
        date="2026-08-03",
        transfer_type="free transfer",
        sources=("transfermarkt",),
        transfer_id_transfermarkt=6481567,
    )

    reconciled = reconcile_transfer_sources([[fotmob], [transfermarkt]])

    assert len(reconciled) == 1
    assert reconciled[0].sources == ("fotmob", "transfermarkt")
    assert reconciled[0].transfer_id_transfermarkt == 6481567
    assert reconciled[0].from_club == "Brentford FC"


def _sortitoutsi_signal() -> Transfer:
    return Transfer(
        "Jordan Henderson",
        "",
        "Chelsea",
        date="2026-08-03",
        sources=("sortitoutsi",),
        proof_urls=("https://www.chelseafc.com/proof",),
        verification_status="enabled",
        infer_from_current_roster=True,
    )


def test_enabled_destination_only_signal_infers_one_exact_current_roster():
    matcher = NameMatcher()
    matcher.load_player_db([("Jordan Henderson", 7)])
    matcher.load_team_db({"Brentford": 10, "Chelsea": 20})

    matched = _match_transfers_statefully(
        [_sortitoutsi_signal()],
        matcher,
        80,
        {10: [7], 20: []},
        {10, 20},
    )[0]

    assert matched.is_club_transfer
    assert matched.from_team_id == 10
    assert matched.to_team_id == 20
    assert matched.transfer.from_club == "Brentford"


def test_destination_only_signal_is_unactionable_when_current_roster_is_ambiguous():
    matcher = NameMatcher()
    matcher.load_player_db([("Jordan Henderson", 7)])
    matcher.load_team_db({"Brentford": 10, "Chelsea": 20, "Duplicate": 30})

    matched = _match_transfers_statefully(
        [_sortitoutsi_signal()],
        matcher,
        80,
        {10: [7], 20: [], 30: [7]},
        {10, 20, 30},
    )[0]

    assert matched.is_fully_matched is False
    assert matched.from_team_id == -1


def test_destination_only_signal_cannot_infer_source_for_unresolved_destination():
    matcher = NameMatcher()
    matcher.load_player_db([("Jordan Henderson", 7)])
    matcher.load_team_db({"Brentford": 10})

    matched = _match_transfers_statefully(
        [_sortitoutsi_signal()],
        matcher,
        80,
        {10: [7]},
        {10},
    )[0]

    assert matched.is_fully_matched is False
    assert matched.from_team_id == -1
    assert matched.to_team_id is None
    assert matched.transfer.from_club == ""


def test_run_pipeline_accepts_transfermarkt_dated_event_without_other_sources(
    monkeypatch,
):
    import run

    transfermarkt = Transfer(
        "Jordan Henderson",
        "Brentford FC",
        "Chelsea FC",
        date="2026-08-03",
        sources=("transfermarkt",),
        player_id_transfermarkt=61651,
        from_club_id_transfermarkt=1148,
        to_club_id_transfermarkt=631,
        transfer_id_transfermarkt=6481567,
    )
    monkeypatch.setattr(run, "fetch_fotmob_transfers", lambda **_: [])
    monkeypatch.setattr(
        run,
        "fetch_major_clubs_transfers_safely",
        lambda **_: [],
    )
    monkeypatch.setattr(run, "fetch_wikipedia_transfers", lambda **_: [])
    monkeypatch.setattr(run, "fetch_sortitoutsi_transfers", lambda **_: [])
    monkeypatch.setattr(
        run,
        "fetch_transfermarkt_transfers",
        lambda **_: [transfermarkt],
    )

    transfers = run._scrape_run_transfers(
        SimpleNamespace(
            popular=False,
            window="summer",
            since="2026-08-03",
            club=None,
            deep=False,
            fotmob_only=False,
        )
    )

    assert transfers == [transfermarkt]


def test_run_pipeline_reconciles_supplemental_sources(monkeypatch):
    import run

    fotmob = Transfer(
        "Jordan Henderson", "Brentford", "Chelsea", date="2026-08-03"
    )
    wikipedia = Transfer(
        "Jordan Henderson",
        "Brentford",
        "Chelsea",
        date="2026-08-03",
        sources=("wikipedia",),
    )
    signal = _sortitoutsi_signal()
    transfermarkt = Transfer(
        "Jordan Henderson",
        "Brentford FC",
        "Chelsea FC",
        date="2026-08-03",
        sources=("transfermarkt",),
        player_id_transfermarkt=61651,
        from_club_id_transfermarkt=1148,
        to_club_id_transfermarkt=631,
        transfer_id_transfermarkt=6481567,
    )
    monkeypatch.setattr(run, "fetch_fotmob_transfers", lambda **_: [fotmob])
    monkeypatch.setattr(
        run,
        "fetch_major_clubs_transfers_safely",
        lambda **_: [],
    )
    monkeypatch.setattr(run, "fetch_wikipedia_transfers", lambda **_: [wikipedia])
    monkeypatch.setattr(run, "fetch_sortitoutsi_transfers", lambda **_: [signal])
    monkeypatch.setattr(
        run,
        "fetch_transfermarkt_transfers",
        lambda **_: [transfermarkt],
        raising=False,
    )

    transfers = run._scrape_run_transfers(
        SimpleNamespace(
            popular=False,
            window="summer",
            since=None,
            club=None,
            deep=False,
            fotmob_only=False,
        )
    )

    assert len(transfers) == 1
    assert transfers[0].sources == (
        "fotmob",
        "wikipedia",
        "transfermarkt",
        "sortitoutsi",
    )


def test_run_pipeline_treats_undated_wikipedia_route_as_corroborator(monkeypatch):
    import run

    fotmob = Transfer(
        "Nathaniel Brown",
        "Eintracht Frankfurt",
        "Bayern Munich",
        date="2026-07-03",
    )
    wikipedia = Transfer(
        "Nathaniel Brown",
        "Eintracht Frankfurt",
        "Bayern Munich",
        sources=("wikipedia",),
        proof_urls=("https://fcbayern.example/brown",),
        verification_status="corroborator",
    )
    monkeypatch.setattr(run, "fetch_fotmob_transfers", lambda **_: [fotmob])
    monkeypatch.setattr(
        run,
        "fetch_major_clubs_transfers_safely",
        lambda **_: [],
    )
    monkeypatch.setattr(run, "fetch_wikipedia_transfers", lambda **_: [wikipedia])
    monkeypatch.setattr(run, "fetch_sortitoutsi_transfers", lambda **_: [])
    monkeypatch.setattr(run, "fetch_transfermarkt_transfers", lambda **_: [])

    transfers = run._scrape_run_transfers(
        SimpleNamespace(
            popular=False,
            window="summer",
            since=None,
            club=None,
            deep=False,
            fotmob_only=False,
        )
    )

    assert len(transfers) == 1
    assert transfers[0].date == "2026-07-03"
    assert transfers[0].sources == ("fotmob", "wikipedia")
    assert transfers[0].proof_urls == ("https://fcbayern.example/brown",)


def test_fotmob_only_flag_does_not_call_supplemental_sources(monkeypatch):
    import run

    monkeypatch.setattr(
        run,
        "fetch_fotmob_transfers",
        lambda **_: [Transfer("Player", "A", "B", date="2026-08-03")],
    )
    monkeypatch.setattr(
        run,
        "fetch_major_clubs_transfers_safely",
        lambda **_: [],
    )
    monkeypatch.setattr(
        run,
        "fetch_wikipedia_transfers",
        lambda **_: (_ for _ in ()).throw(AssertionError("Wikipedia called")),
    )
    monkeypatch.setattr(
        run,
        "fetch_sortitoutsi_transfers",
        lambda **_: (_ for _ in ()).throw(AssertionError("Sortitoutsi called")),
    )
    monkeypatch.setattr(
        run,
        "fetch_transfermarkt_transfers",
        lambda: (_ for _ in ()).throw(AssertionError("Transfermarkt called")),
        raising=False,
    )

    transfers = run._scrape_run_transfers(
        SimpleNamespace(
            popular=False,
            window="summer",
            since=None,
            club=None,
            deep=False,
            fotmob_only=True,
        )
    )

    assert len(transfers) == 1
    assert transfers[0].sources == ("fotmob",)
