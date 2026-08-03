"""Fixtures and safety rules for supplemental transfer sources."""

from datetime import date
from types import SimpleNamespace

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
    monkeypatch.setattr(run, "fetch_fotmob_transfers", lambda **_: [fotmob])
    monkeypatch.setattr(run, "fetch_wikipedia_transfers", lambda **_: [wikipedia])
    monkeypatch.setattr(run, "fetch_sortitoutsi_transfers", lambda **_: [signal])

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
    assert transfers[0].sources == ("fotmob", "wikipedia", "sortitoutsi")


def test_fotmob_only_flag_does_not_call_supplemental_sources(monkeypatch):
    import run

    monkeypatch.setattr(
        run,
        "fetch_fotmob_transfers",
        lambda **_: [Transfer("Player", "A", "B", date="2026-08-03")],
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
