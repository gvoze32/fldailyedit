"""
Tests for the fuzzy name matcher.
"""
import pytest
from scraper.matcher import NameMatcher, _is_position_compatible
from scraper.text import fold_text


# --- Normalization tests ---

class TestNormalize:
    def test_lowercase(self):
        assert fold_text("MESSI") == "messi"

    def test_strip_diacritics(self):
        assert fold_text("Mbappé") == "mbappe"
        assert fold_text("Müller") == "muller"
        assert fold_text("Señor") == "senor"
        assert fold_text("Čech") == "cech"

    def test_collapse_whitespace(self):
        assert fold_text("  Lionel   Messi  ") == "lionel messi"

    def test_combined(self):
        assert fold_text("  Kylian  Mbappé  ") == "kylian mbappe"

    def test_transliterate_non_decomposing_letters(self):
        assert fold_text("Kenan Yıldız") == "kenan yildiz"



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

    def test_exact_name_with_conflicting_age_is_rejected(self):
        matcher = NameMatcher()
        matcher.load_player_db(
            {"Reece James": 126046},
            positions={126046: "RB"},
            ages={126046: 26},
        )

        player_id, matched_name, confidence = matcher.match_player(
            "Reece James",
            position="Defender",
            age=32,
        )

        assert (player_id, matched_name, confidence) == (None, "", 100.0)

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


    def test_compound_surname_suffix_matches_base_name(self):
        matcher = NameMatcher()
        matcher.load_player_db({"Khéphren Thuram": 1011})

        player_id, matched_name, confidence = matcher.match_player(
            "Khéphren Thuram-Ulien",
            threshold=80,
        )

        assert player_id == 1011
        assert matched_name == "Khéphren Thuram"
        assert confidence >= 95


    def test_provider_short_name_matches_suffix_variant_with_metadata(self):
        matcher = NameMatcher()
        matcher.load_player_db(
            {"Neymar Jr": 1011, "Neymar Uribe": 1012},
            positions={1011: "SS"},
            ages={1011: 33},
        )

        player_id, matched_name, confidence = matcher.match_player(
            "Neymar",
            threshold=80,
            position="CAM",
            age=34,
        )

        assert player_id == 1011
        assert matched_name == "Neymar Jr"
        assert confidence >= 95

    def test_middle_name_token_set(self, matcher):
        """'Gabriel Jesus' matching 'Gabriel Fernando de Jesus'."""
        matcher.load_player_db({"Gabriel Fernando de Jesus": 5001})
        pid, name, conf = matcher.match_player("Gabriel Jesus", threshold=75)
        assert pid == 5001
        assert conf >= 75
    def test_first_and_last_tokens_survive_inserted_middle_name(self):
        matcher = NameMatcher()
        matcher.load_player_db({"Pierre Højbjerg": 1011})

        player_id, matched_name, confidence = matcher.match_player(
            "Pierre-Emile Højbjerg",
            threshold=80,
        )

        assert player_id == 1011
        assert matched_name == "Pierre Højbjerg"
        assert confidence >= 95


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



    def test_unknown_age_cannot_lose_to_known_duplicate(self):
        matcher = NameMatcher()
        matcher.load_player_db(
            players=[("João Pedro", 9001), ("João Pedro", 9002)],
            ages={9001: 24},
        )

        player_id, matched_name, confidence = matcher.match_player(
            "João Pedro",
            age=21,
        )

        assert (player_id, matched_name, confidence) == (None, "", 100.0)

    def test_known_position_line_rejects_other_position_line(self):
        matcher = NameMatcher()
        matcher.load_player_db(
            players=[("João Pedro", 9001), ("João Pedro", 9002)],
            positions={9001: "CF", 9002: "DMF"},
        )

        player_id, matched_name, confidence = matcher.match_player(
            "João Pedro",
            position="Midfielder",
        )

        assert (player_id, matched_name, confidence) == (9002, "João Pedro", 100.0)


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

    def test_metadata_cannot_drop_stronger_name_match(self):
        """Age agreement must not promote a weaker name over a stronger one."""
        m = NameMatcher()
        m.load_player_db(
            players={"Bruno Silva Santos": 8101, "Bruno Sousa": 8102},
            ages={8101: 25, 8102: 27},
        )

        pid, name, conf = m.match_player("Bruno Silva", threshold=70, age=27)

        assert (pid, name) == (8101, "Bruno Silva Santos")
        assert conf >= 95

    def test_tied_candidates_beyond_ten_are_still_scored(self):
        """Every tied fuzzy candidate competes, not just the first ten."""
        m = NameMatcher()
        players = {f"Rodrigo Player{index:02d}": 8200 + index for index in range(12)}
        m.load_player_db(
            players=players,
            nationalities={
                **{pid: "Brazil" for pid in players.values()},
                8211: "Spain",
            },
        )

        pid, name, conf = m.match_player("Rodrigo", nationality="Spain")

        assert (pid, name) == (8211, "Rodrigo Player11")
        assert conf == 100.0

    @pytest.mark.parametrize(
        ("provider_position", "pes_position"),
        [("LW", "LMF"), ("RW", "RMF"), ("LM", "LWF"), ("RM", "RB")],
    )
    def test_wide_players_match_across_lines(self, provider_position, pes_position):
        assert _is_position_compatible(provider_position, pes_position)

        m = NameMatcher()
        m.load_player_db(
            players={"Alejandro Garnacho": 8301},
            positions={8301: pes_position},
        )

        assert m.match_player("Alejandro Garnacho", position=provider_position) == (
            8301,
            "Alejandro Garnacho",
            100.0,
        )

    def test_central_lines_stay_incompatible(self):
        assert not _is_position_compatible("CF", "DMF")
        assert not _is_position_compatible("LW", "CB")




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

    def test_no_bundled_team_aliases_are_loaded(self):
        m = NameMatcher()
        m.load_team_db({"Manchester United": 100})
        tid, _, conf = m.match_team("Man Utd")
        assert (tid, conf) != (100, 100.0)

    def test_supplied_alias_target_can_resolve_through_club_affix(self):
        m = NameMatcher()
        m.load_team_db({"Juventus FC": 120})
        m.load_team_aliases({"Juve": "Juventus"})
        assert m.match_team("Juve") == (120, "Juventus FC", 100.0)

    def test_club_affix_variant_matches_cleaned_name(self):
        m = NameMatcher()
        m.load_team_db({"Lion City Sailors": 71134})

        assert m.match_team("Lion City Sailors FC") == (
            71134,
            "Lion City Sailors",
            98.0,
        )

    @pytest.mark.parametrize("name", ["Free Agent", "Without Club", "Retired", ""])
    def test_non_club_sentinel_is_never_fuzzy_matched(self, matcher, name):
        tid, matched_name, conf = matcher.match_team(name)
        assert tid is None
        assert matched_name == ""
        assert conf == 100.0

    def test_data_derived_alias_match(self, matcher):
        matcher.load_team_aliases({"Bayern Munich": "FC Bayern München"})
        tid, name, conf = matcher.match_team("Bayern Munich")
        assert (tid, conf) == (2005, 100.0)

    def test_alias_lookup_normalizes_diacritics(self, matcher):
        matcher.load_team_aliases({"Atlético de Madrid": "Atletico Madrid"})
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


class TestTokenSetIndex:
    def test_matches_process_extract_exactly(self):
        import random

        from rapidfuzz import fuzz, process

        from scraper.matcher import _TokenSetIndex

        rng = random.Random(7)
        letters = "abcdeilmnorstu"

        def word():
            return "".join(rng.choice(letters) for _ in range(rng.randint(2, 7)))

        names = list(dict.fromkeys(
            " ".join(word() for _ in range(rng.randint(1, 3))) for _ in range(600)
        ))
        index = _TokenSetIndex(names)
        queries = [" ".join(word() for _ in range(rng.randint(1, 3))) for _ in range(150)]
        queries += names[:50] + [names[0].split()[0], ""]
        for cutoff in (0.0, 55.0, 68.0, 83.0):
            for query in queries:
                expected = [
                    tuple(item)
                    for item in process.extract(
                        query,
                        names,
                        scorer=fuzz.token_set_ratio,
                        limit=None,
                        score_cutoff=cutoff,
                    )
                ]
                assert index.extract(query, cutoff) == expected
