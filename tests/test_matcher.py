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

    def test_override_metadata_is_not_treated_as_player_alias(self):
        assert "_comment" not in NameMatcher()._player_overrides

    def test_middle_name_token_set(self, matcher):
        """'Gabriel Jesus' matching 'Gabriel Fernando de Jesus'."""
        matcher.load_player_db({"Gabriel Fernando de Jesus": 5001})
        pid, name, conf = matcher.match_player("Gabriel Jesus", threshold=75)
        assert pid == 5001
        assert conf >= 75

    def test_contextual_disambiguation(self):
        """When multiple players share similar names, context chooses the one on from_team."""
        m = NameMatcher()
        m.load_player_db({
            "Danilo Luiz da Silva": 3001,   # Juventus
            "Danilo Pereira": 3002,         # PSG
        })
        # Roster: 2007 (Juventus) has 3001, 2006 (PSG) has 3002
        roster_map = {
            2007: [3001, 9999],
            2006: [3002, 8888],
        }
        # Searching "Danilo" with origin team Juventus should pick 3001
        pid, name, conf = m.match_player(
            "Danilo",
            threshold=70,
            from_team_id=2007,
            team_player_map=roster_map,
        )
        assert pid == 3001
        assert conf == 100.0

    def test_roster_context_rejects_near_name_collision(self):
        """Roster context must not turn a weak name similarity into identity."""
        m = NameMatcher()
        m.load_player_db({
            "Diney Borges": 58182,
            "Diego Torres": 58183,
        })

        pid, name, confidence = m.match_player(
            "Diego Borges",
            threshold=80,
            from_team_id=1667,
            team_player_map={1667: [58182]},
        )

        assert (pid, name) == (None, "")
        assert confidence < 90

    def test_identical_names_require_context(self):
        """Duplicate normalized names must never be resolved by insertion order."""
        m = NameMatcher()
        m.load_player_db([
            ("Patrick", 3001),
            ("Patrick", 3002),
        ])

        pid, _, conf = m.match_player("Patrick")
        assert pid is None
        assert conf == 100.0

        pid, name, conf = m.match_player(
            "Patrick",
            from_team_id=2007,
            team_player_map={2007: [3002]},
        )
        assert pid == 3002
        assert name == "Patrick"
        assert conf == 100.0

    def test_source_roster_has_priority_over_destination(self):
        """Duplicate names resolve to the source player, not an arbitrary union member."""
        m = NameMatcher()
        m.load_player_db([("Patrick", 3001), ("Patrick", 3002)])

        pid, name, conf = m.match_player(
            "Patrick",
            from_team_id=10,
            to_team_id=20,
            team_player_map={10: [3001], 20: [3002]},
        )
        assert (pid, name, conf) == (3001, "Patrick", 100.0)

    def test_roster_context_does_not_bypass_threshold(self):
        m = NameMatcher()
        m.load_player_db({"Alice Brown": 4001})

        pid, _, conf = m.match_player(
            "Zzzzz Unknown",
            threshold=95,
            from_team_id=10,
            team_player_map={10: [4001]},
        )
        assert pid is None
        assert conf < 95

    def test_position_compatibility_gk_protection(self):
        """A goalkeeper transfer should not match an outfield player of same name."""
        m = NameMatcher()
        m.load_player_db(
            players={"David Raya": 7001, "David Raya Silva": 7002},
            positions={7001: "GK", 7002: "CF"}
        )
        # Looking for GK should pick 7001
        pid, name, conf = m.match_player("David Raya", position="GK")
        assert pid == 7001

        # Looking for ST should NOT pick GK 7001
        pid, name, conf = m.match_player("David Raya", position="ST")
        assert pid == 7002

    def test_keeper_labels_are_treated_as_goalkeeper(self):
        matcher = NameMatcher()
        matcher.load_player_db(
            players=[("David Raya", 7001), ("David Raya", 7002)],
            positions={7001: "GK", 7002: "CF"},
        )

        for label in ("Keeper", "Goalkeeper", "Goalie"):
            player_id, _, _ = matcher.match_player("David Raya", position=label)
            assert player_id == 7001


    def test_tri_factor_nationality_and_age_disambiguation(self):
        """Disambiguate identical or very similar names using nationality and age."""
        m = NameMatcher()
        m.load_player_db(
            players={
                "Gabriel Magalhaes": 8001,
                "Gabriel Jesus": 8002,
                "Gabriel Paulista": 8003,
            },
            positions={8001: "CB", 8002: "CF", 8003: "CB"},
            nationalities={8001: "Brazil", 8002: "Brazil", 8003: "Spain"},
            ages={8001: 27, 8002: 27, 8003: 34},
        )
        # Search for Gabriel with CB position and Spain nationality / 34 age
        pid, name, conf = m.match_player("Gabriel", position="CB", nationality="Spain", age=34)
        assert pid == 8003
        assert name == "Gabriel Paulista"

        # Search for Gabriel with CB position and Brazil nationality / 27 age
        pid, name, conf = m.match_player("Gabriel", position="CB", nationality="Brazil", age=27)
        assert pid == 8001
        assert name == "Gabriel Magalhaes"




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
            "AC Sparta Praha": 2009,
            "Atletico Madrid": 2010,
        })
        return m

    def test_exact_match(self, matcher):
        tid, name, conf = matcher.match_team("Manchester United")
        assert tid == 2001
        assert conf == 100.0

    def test_low_id_club_is_not_mistaken_for_national_team(self):
        m = NameMatcher()
        m.load_team_db({"Manchester United": 100})
        assert m.match_team("Man Utd") == (100, "Manchester United", 100.0)

    def test_alias_target_can_resolve_through_club_affix(self):
        m = NameMatcher()
        m.load_team_db({"Juventus FC": 120})
        assert m.match_team("Juve") == (120, "Juventus FC", 100.0)

    @pytest.mark.parametrize("name", ["Free Agent", "Without Club", "Retired", ""])
    def test_non_club_sentinel_is_never_fuzzy_matched(self, matcher, name):
        tid, matched_name, conf = matcher.match_team(name)
        assert tid is None
        assert matched_name == ""
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

    def test_alias_lookup_normalizes_diacritics(self, matcher):
        tid, name, conf = matcher.match_team("Atletico de Madrid")
        assert (tid, name, conf) == (2010, "Atletico Madrid", 100.0)

    def test_fuzzy_team(self, matcher):
        """'Barcelona' should fuzzy-match 'FC Barcelona'."""
        tid, name, conf = matcher.match_team("Barcelona", threshold=60)
        assert tid == 2003
        assert conf >= 60

    def test_club_affix_cleaning(self, matcher):
        """'Sparta Praha' or 'Sparta Prague' should match 'AC Sparta Praha'."""
        tid, name, conf = matcher.match_team("Sparta Praha", threshold=80)
        assert tid == 2009
        assert conf >= 80

    def test_ambiguous_affix_cleaned_team_is_not_guessed(self):
        m = NameMatcher()
        m.load_team_db({
            "FC Barcelona": 2003,
            "Barcelona SC": 2658,
        })

        tid, name, conf = m.match_team("Barcelona", threshold=60)

        assert tid is None
        assert name == ""
        assert conf >= 60

    def test_no_match(self, matcher):
        tid, name, conf = matcher.match_team("Nonexistent FC", threshold=80)
        assert tid is None

    def test_empty_db(self):
        m = NameMatcher()
        tid, name, conf = m.match_team("Any Team")
        assert tid is None
        assert conf == 0.0
