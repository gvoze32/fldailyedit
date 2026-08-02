"""
Tests for the fuzzy name matcher.
"""
import pytest
from scraper.matcher import NameMatcher, _normalize


# --- Normalization tests ---

class TestNormalize:
    def test_lowercase(self):
        assert _normalize("MESSI") == "messi"

    def test_strip_diacritics(self):
        assert _normalize("Mbappé") == "mbappe"
        assert _normalize("Müller") == "muller"
        assert _normalize("Señor") == "senor"
        assert _normalize("Čech") == "cech"

    def test_collapse_whitespace(self):
        assert _normalize("  Lionel   Messi  ") == "lionel messi"

    def test_combined(self):
        assert _normalize("  Kylian  Mbappé  ") == "kylian mbappe"


# --- Player matching tests ---

class TestPlayerMatching:
    @pytest.fixture
    def matcher(self):
        m = NameMatcher()
        m.load_player_db({
            "Lionel Messi": 1001,
            "Kylian Mbappé": 1002,
            "Cristiano Ronaldo": 1003,
            "Robert Lewandowski": 1004,
            "Erling Haaland": 1005,
            "Kevin De Bruyne": 1006,
            "Mohamed Salah": 1007,
            "Thomas Müller": 1008,
            "Neymar": 1009,
            "Luka Modrić": 1010,
        })
        return m

    def test_exact_match(self, matcher):
        pid, name, conf = matcher.match_player("Lionel Messi")
        assert pid == 1001
        assert conf == 100.0

    def test_case_insensitive(self, matcher):
        pid, name, conf = matcher.match_player("lionel messi")
        assert pid == 1001
        assert conf == 100.0

    def test_diacritics_ignored(self, matcher):
        pid, name, conf = matcher.match_player("Kylian Mbappe")
        assert pid == 1002
        assert conf == 100.0

    def test_diacritics_ignored_2(self, matcher):
        pid, name, conf = matcher.match_player("Thomas Muller")
        assert pid == 1008
        assert conf == 100.0

    def test_fuzzy_abbreviation(self, matcher):
        """K. Mbappé should match Kylian Mbappé with high confidence."""
        pid, name, conf = matcher.match_player("K. Mbappé", threshold=60)
        assert pid == 1002
        assert conf >= 60

    def test_fuzzy_word_order(self, matcher):
        """Ronaldo Cristiano should match Cristiano Ronaldo."""
        pid, name, conf = matcher.match_player("Ronaldo Cristiano", threshold=70)
        assert pid == 1003
        assert conf >= 70

    def test_no_match_below_threshold(self, matcher):
        """Completely unrelated name should not match."""
        pid, name, conf = matcher.match_player("John Smith Unknown Player", threshold=80)
        assert pid is None

    def test_partial_name(self, matcher):
        """Just 'Haaland' should match 'Erling Haaland'."""
        pid, name, conf = matcher.match_player("Haaland", threshold=60)
        assert pid == 1005
        assert conf >= 60

    def test_empty_db(self):
        m = NameMatcher()
        pid, name, conf = m.match_player("Anyone")
        assert pid is None
        assert conf == 0.0


# --- Team matching tests ---

class TestTeamMatching:
    @pytest.fixture
    def matcher(self):
        m = NameMatcher()
        m.load_team_db({
            "Manchester United": 2001,
            "Manchester City": 2002,
            "FC Barcelona": 2003,
            "Real Madrid": 2004,
            "FC Bayern München": 2005,
            "Paris Saint-Germain": 2006,
            "Juventus": 2007,
            "Inter Milan": 2008,
        })
        return m

    def test_exact_match(self, matcher):
        tid, name, conf = matcher.match_team("Manchester United")
        assert tid == 2001
        assert conf == 100.0

    def test_alias_match(self, matcher):
        """'Man Utd' should resolve via aliases to 'Manchester United'."""
        tid, name, conf = matcher.match_team("Man Utd")
        assert tid == 2001
        assert conf == 100.0

    def test_alias_psg(self, matcher):
        tid, name, conf = matcher.match_team("PSG")
        assert tid == 2006
        assert conf == 100.0

    def test_alias_bayern(self, matcher):
        tid, name, conf = matcher.match_team("Bayern Munich")
        assert tid == 2005
        assert conf == 100.0

    def test_fuzzy_team(self, matcher):
        """'Barcelona' should fuzzy-match 'FC Barcelona'."""
        tid, name, conf = matcher.match_team("Barcelona", threshold=60)
        assert tid == 2003
        assert conf >= 60

    def test_no_match(self, matcher):
        tid, name, conf = matcher.match_team("Nonexistent FC", threshold=80)
        assert tid is None

    def test_empty_db(self):
        m = NameMatcher()
        tid, name, conf = m.match_team("Any Team")
        assert tid is None
        assert conf == 0.0
