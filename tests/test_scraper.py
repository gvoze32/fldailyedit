"""
Tests for the Transfermarkt scraper — uses saved HTML fixtures to avoid hitting the live site.
"""
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
            "fromClub": "Paris Saint-Germain",
            "toClub": "Real Madrid",
            "transferType": {"text": "free transfer"},
            "fee": {"feeText": "Free"},
            "marketValue": "€180m",
            "transferDate": "2024-07-01",
        }
        scraper = FotmobScraper()
        t = scraper._parse_fotmob_item(item)
        assert t is not None
        assert t.player_name == "Kylian Mbappé"
        assert t.from_club == "Paris Saint-Germain"
        assert t.to_club == "Real Madrid"
        assert t.transfer_type == "free transfer"
        assert t.fee == "Free"
        assert t.date == "2024-07-01"

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

