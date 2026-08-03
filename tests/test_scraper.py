"""
Tests for the FotMob scraper and transfer models.
"""
import asyncio

import pytest
from scraper.models import Transfer


class TestTransferModel:
    def test_basic_creation(self):
        t = Transfer(
            player_name="Kylian Mbappé",
            from_club="Paris Saint-Germain",
            to_club="Real Madrid",
            transfer_type="transfer",
        )
        assert t.player_name == "Kylian Mbappé"
        assert t.from_club == "Paris Saint-Germain"
        assert t.to_club == "Real Madrid"

    def test_str(self):
        t = Transfer(
            player_name="Mbappé",
            from_club="PSG",
            to_club="Real Madrid",
        )
        s = str(t)
        assert "Mbappé" in s
        assert "PSG" in s
        assert "Real Madrid" in s

    def test_defaults(self):
        t = Transfer(player_name="Test", from_club="A", to_club="B")
        assert t.transfer_type == "transfer"
        assert t.fee == ""
        assert t.league == ""
        assert t.season == ""
        assert t.date == ""


class TestMatchedTransfer:
    def test_fully_matched(self):
        from scraper.models import MatchedTransfer

        t = Transfer(player_name="Messi", from_club="PSG", to_club="Inter Miami")
        mt = MatchedTransfer(
            transfer=t,
            player_id=1001,
            from_team_id=2001,
            to_team_id=2002,
            player_confidence=95.0,
            from_team_confidence=90.0,
            to_team_confidence=85.0,
        )
        assert mt.is_fully_matched
        assert mt.min_confidence == 85.0

    def test_partial_match(self):
        from scraper.models import MatchedTransfer

        t = Transfer(player_name="Unknown", from_club="A", to_club="B")
        mt = MatchedTransfer(transfer=t, player_id=None)
        assert not mt.is_fully_matched

    def test_str_matched(self):
        from scraper.models import MatchedTransfer

        t = Transfer(player_name="Messi", from_club="PSG", to_club="Inter Miami")
        mt = MatchedTransfer(
            transfer=t,
            player_id=1001,
            from_team_id=2001,
            to_team_id=2002,
            player_confidence=95.0,
            from_team_confidence=90.0,
            to_team_confidence=85.0,
        )
        s = str(mt)
        assert "✓" in s

    def test_str_unmatched(self):
        from scraper.models import MatchedTransfer

        t = Transfer(player_name="Unknown", from_club="A", to_club="B")
        mt = MatchedTransfer(transfer=t)
        s = str(mt)
        assert "✗" in s





class TestFotmobScraper:
    def test_parse_fotmob_item_transfer(self):
        from scraper.fotmob import FotmobScraper

        item = {
            "name": "Kylian Mbappé",
            "position": {"label": "CF", "key": "forward_short"},
            "fromClub": "Paris Saint-Germain",
            "fromClubFullName": "Paris Saint-Germain",
            "fromClubId": 9843,
            "toClub": "Real Madrid",
            "toClubFullName": "Real Madrid",
            "toClubId": 8633,
            "transferType": {"text": "free transfer"},
            "fee": {"feeText": "Free"},
            "marketValue": 180000000,
            "onLoan": False,
            "contractExtension": False,
            "transferDate": "2024-07-01",
        }
        scraper = FotmobScraper()
        t = scraper._parse_fotmob_item(item)
        assert t is not None
        assert t.player_name == "Kylian Mbappé"
        assert t.position == "CF"
        assert t.from_club == "Paris Saint-Germain"
        assert t.to_club == "Real Madrid"
        assert t.transfer_type == "free transfer"
        assert t.fee == "Free"
        assert t.date == "2024-07-01"
        assert t.is_loan is False
        assert t.market_value == 180000000
        assert t.from_club_id_fotmob == 9843

    def test_parse_fotmob_item_loan(self):
        from scraper.fotmob import FotmobScraper

        item = {
            "name": "Alejandro Garnacho",
            "position": {"label": "LW"},
            "fromClub": "Chelsea",
            "toClub": "Aston Villa",
            "onLoan": True,
            "fee": {"feeText": "loan"},
            "transferDate": "2026-08-01",
        }
        scraper = FotmobScraper()
        t = scraper._parse_fotmob_item(item)
        assert t is not None
        assert t.is_loan is True
        assert t.transfer_type == "loan"
        assert t.position == "LW"

    def test_parse_fotmob_contract_extension_ignored(self):
        from scraper.fotmob import FotmobScraper

        item = {
            "name": "Bukayo Saka",
            "fromClub": "Arsenal",
            "toClub": "Arsenal",
            "contractExtension": True,
            "transferDate": "2026-08-01",
        }
        scraper = FotmobScraper()
        t = scraper._parse_fotmob_item(item, ignore_extensions=True)
        assert t is None

    def test_parse_fotmob_item_missing_name(self):
        from scraper.fotmob import FotmobScraper

        item = {
            "name": "",
            "fromClub": "PSG",
            "toClub": "Real Madrid",
        }
        scraper = FotmobScraper()
        t = scraper._parse_fotmob_item(item)
        assert t is None


class TestTransferWindowLogic:
    def test_window_range_summer(self):
        from datetime import date
        from scraper.fotmob import get_transfer_window_range

        ref = date(2026, 8, 15)
        start, end = get_transfer_window_range("summer", ref_date=ref)
        assert start == date(2026, 6, 1)
        assert end == date(2026, 9, 30)

    def test_window_range_winter(self):
        from datetime import date
        from scraper.fotmob import get_transfer_window_range

        ref = date(2026, 1, 15)
        start, end = get_transfer_window_range("winter", ref_date=ref)
        assert start == date(2026, 1, 1)
        assert end == date(2026, 2, 28)

    def test_window_range_auto_summer(self):
        from datetime import date
        from scraper.fotmob import get_transfer_window_range

        ref = date(2026, 7, 1)
        start, end = get_transfer_window_range("auto", ref_date=ref)
        assert start == date(2026, 6, 1)
        assert end == date(2026, 9, 30)

    def test_window_range_auto_winter(self):
        from datetime import date
        from scraper.fotmob import get_transfer_window_range

        ref = date(2026, 2, 1)
        start, end = get_transfer_window_range("auto", ref_date=ref)
        assert start == date(2026, 1, 1)
        assert end == date(2026, 2, 28)

    def test_window_range_auto_between_windows_is_bounded(self):
        from datetime import date
        from scraper.fotmob import get_transfer_window_range

        assert get_transfer_window_range("auto", date(2026, 4, 10)) == (
            date(2026, 1, 1), date(2026, 2, 28)
        )
        assert get_transfer_window_range("auto", date(2026, 11, 10)) == (
            date(2026, 6, 1), date(2026, 9, 30)
        )

    def test_window_range_winter_handles_leap_year(self):
        from datetime import date
        from scraper.fotmob import get_transfer_window_range

        assert get_transfer_window_range("winter", date(2028, 1, 10))[1] == date(2028, 2, 29)

    def test_window_range_all(self):
        from datetime import date
        from scraper.fotmob import get_transfer_window_range

        start, end = get_transfer_window_range("all")
        assert start == date(2000, 1, 1)
        assert end is None

    def test_parse_iso_date(self):
        from datetime import date
        from scraper.fotmob import parse_iso_date

        assert parse_iso_date("2026-08-02T13:11:45Z") == date(2026, 8, 2)
        assert parse_iso_date("2026-01-15") == date(2026, 1, 15)
        assert parse_iso_date("") is None
        assert parse_iso_date("invalid-date") is None

    def test_invalid_explicit_since_date_is_rejected(self):
        from scraper.fotmob import _resolve_date_range

        with pytest.raises(ValueError, match="expected YYYY-MM-DD"):
            _resolve_date_range("03/08/2026", "auto")

    def test_effective_range_never_includes_future_transfer(self):
        from datetime import date
        from scraper.fotmob import _resolve_date_range

        assert _resolve_date_range(
            "2026-07-28", "auto", ref_date=date(2026, 8, 3)
        ) == (date(2026, 7, 28), date(2026, 8, 3))
        assert _resolve_date_range(
            None, "summer", ref_date=date(2026, 8, 3)
        ) == (date(2026, 6, 1), date(2026, 8, 3))


class TestScraperSafety:
    def test_global_feed_does_not_stop_on_old_item_in_last_modified_order(self, monkeypatch):
        from scraper import fotmob

        pages = [
            {
                "transfers": [
                    {
                        "name": "Recently Corrected Old Deal",
                        "fromClub": "A",
                        "toClub": "B",
                        "transferDate": "2026-01-01",
                    },
                    {
                        "name": "Current Page Deal",
                        "fromClub": "A",
                        "toClub": "B",
                        "transferDate": "2026-08-01",
                    },
                ]
            },
            {
                "transfers": [{
                    "name": "Next Page Deal",
                    "fromClub": "C",
                    "toClub": "D",
                    "transferDate": "2026-08-02",
                }]
            },
            {"transfers": []},
        ]

        class FakeResponse:
            status = 200

            def __init__(self, payload):
                self.payload = payload

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def json(self, content_type=None):
                return self.payload

        class FakeSession:
            def __init__(self, *_, **__):
                self.page = 0

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            def get(self, _):
                payload = pages[self.page]
                self.page += 1
                return FakeResponse(payload)

        monkeypatch.setattr(fotmob.aiohttp, "ClientSession", FakeSession)
        transfers = asyncio.run(
            fotmob.FotmobScraper()._fetch_transfers_async(
                max_pages=3,
                since_date="2026-07-28",
            )
        )
        assert [transfer.player_name for transfer in transfers] == [
            "Current Page Deal",
            "Next Page Deal",
        ]

    def test_bounded_window_skips_undated_transfer(self):
        from datetime import date
        from scraper.fotmob import FotmobScraper

        payload = {
            "transfers": {
                "data": {
                    "Players in": [
                        {"name": "No Date", "fromClub": "A", "toClub": "B"},
                        {
                            "name": "Dated",
                            "fromClub": "A",
                            "toClub": "B",
                            "transferDate": "2026-07-01",
                        },
                    ]
                }
            }
        }
        results = FotmobScraper()._extract_transfers_from_team_data(
            payload, date(2026, 6, 1), date(2026, 9, 30)
        )
        assert [transfer.player_name for transfer in results] == ["Dated"]

    def test_bad_squad_member_does_not_discard_valid_members(self):
        from scraper.fotmob import FotmobScraper

        payload = {
            "squad": {
                "squad": [{
                    "members": [
                        {"name": "Broken", "shirtNumber": "not-a-number"},
                        {"name": "Valid", "shirtNumber": "17", "role": None},
                    ]
                }]
            }
        }
        results = FotmobScraper()._extract_squad_from_team_data(payload, 42, "Example FC")
        assert len(results) == 1
        assert results[0].player_name == "Valid"
        assert results[0].shirt_number == 17
        assert results[0].to_club == "Example FC"

    def test_club_target_resolution_rejects_ambiguous_substring(self):
        from scraper.fotmob import _resolve_club_targets

        available = {"Manchester United": 1, "Manchester City": 2, "Arsenal": 3}
        assert _resolve_club_targets(["Arsenal"], available) == [("Arsenal", 3)]
        assert _resolve_club_targets(["Manchester"], available) == []

    def test_merge_normalizes_diacritics_and_enriches_duplicate(self):
        from scraper.fotmob import merge_transfers

        first = Transfer("Kylian Mbappé", "Paris SG", "Real Madrid", date="2026-07-01")
        second = Transfer(
            "Kylian Mbappe",
            "Paris SG",
            "Real Madrid",
            date="2026-07-01T10:00:00Z",
            position="CF",
            market_value=180_000_000,
        )
        merged = merge_transfers([[first], [second]])
        assert len(merged) == 1
        assert merged[0].position == "CF"
        assert merged[0].market_value == 180_000_000
