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


class TestScraperParsing:
    def test_parse_transfer_row_arrival(self):
        from parsel import Selector
        from scraper.transfermarkt import _parse_transfer_row

        html = """
        <tr>
            <td class="hauptlink"><a class="spielprofil_tooltip" href="/player1">Kylian Mbappé</a></td>
            <td><img class="tiny_wappen" title="Paris Saint-Germain" src="psg.png"/></td>
            <td class="rechts"><a href="/fee">€180.00m</a></td>
        </tr>
        """
        sel = Selector(text=html).css("tr")[0]
        transfer = _parse_transfer_row(
            row=sel,
            club_name="Real Madrid",
            table_idx=0,
            league_name="La Liga",
            season="2025",
        )
        assert transfer is not None
        assert transfer.player_name == "Kylian Mbappé"
        assert transfer.from_club == "Paris Saint-Germain"
        assert transfer.to_club == "Real Madrid"
        assert transfer.transfer_type == "transfer"
        assert transfer.fee == "€180.00m"
        assert transfer.league == "La Liga"
        assert transfer.season == "2025"

    def test_parse_transfer_row_departure_loan(self):
        from parsel import Selector
        from scraper.transfermarkt import _parse_transfer_row

        html = """
        <tr>
            <td class="hauptlink"><a class="spielprofil_tooltip" href="/player2">Endrick</a></td>
            <td><img class="tiny_wappen" title="Real Valladolid" src="valladolid.png"/></td>
            <td>Loan fee: €1.00m</td>
        </tr>
        """
        sel = Selector(text=html).css("tr")[0]
        transfer = _parse_transfer_row(
            row=sel,
            club_name="Real Madrid",
            table_idx=1,
            league_name="La Liga",
            season="2025",
        )
        assert transfer is not None
        assert transfer.player_name == "Endrick"
        assert transfer.from_club == "Real Madrid"
        assert transfer.to_club == "Real Valladolid"
        assert transfer.transfer_type == "loan"

    def test_parse_transfer_row_end_of_loan(self):
        from parsel import Selector
        from scraper.transfermarkt import _parse_transfer_row

        html = """
        <tr>
            <td class="hauptlink"><a href="/player3">Arda Güler</a></td>
            <td class="zentriert"><img title="Fenerbahce" src="fb.png"/></td>
            <td>End of loan</td>
        </tr>
        """
        sel = Selector(text=html).css("tr")[0]
        transfer = _parse_transfer_row(
            row=sel,
            club_name="Real Madrid",
            table_idx=0,
            league_name="La Liga",
            season="2025",
        )
        assert transfer is not None
        assert transfer.player_name == "Arda Güler"
        assert transfer.from_club == "Fenerbahce"
        assert transfer.to_club == "Real Madrid"
        assert transfer.transfer_type == "end of loan"

    def test_parse_transfer_row_invalid(self):
        from parsel import Selector
        from scraper.transfermarkt import _parse_transfer_row

        html = "<tr><td>No data here</td></tr>"
        sel = Selector(text=html).css("tr")[0]
        transfer = _parse_transfer_row(
            row=sel,
            club_name="Real Madrid",
            table_idx=0,
            league_name="La Liga",
            season="2025",
        )
        assert transfer is None

