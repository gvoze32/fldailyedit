//! Behavioral tests of the matcher, including regressions for nationality
//! substring matching, alias confidence and typed "no match" results.

use std::collections::HashMap;

use super::*;

fn players(records: &[(&str, u32)]) -> NameMatcher {
    NameMatcher::new(
        records.iter().map(|(n, id)| ((*n).to_owned(), *id)),
        HashMap::new(),
        Vec::new(),
    )
}

fn with_attrs(records: &[(&str, u32)], attrs: &[(u32, &str, &str, u32)]) -> NameMatcher {
    NameMatcher::new(
        records.iter().map(|(n, id)| ((*n).to_owned(), *id)),
        attrs
            .iter()
            .map(|&(id, position, nationality, age)| {
                (
                    id,
                    PlayerAttrs {
                        position: position.to_owned(),
                        nationality: nationality.to_owned(),
                        age,
                    },
                )
            })
            .collect(),
        Vec::new(),
    )
}

fn teams(records: &[(&str, u32)]) -> NameMatcher {
    NameMatcher::new(
        Vec::new(),
        HashMap::new(),
        records.iter().map(|(n, id)| ((*n).to_owned(), *id)),
    )
}

fn top(threshold: f64) -> PlayerQuery<'static> {
    PlayerQuery {
        threshold,
        ..PlayerQuery::default()
    }
}

fn tuple(m: PlayerMatch) -> (Option<u32>, String, f64) {
    (m.id, m.name, m.confidence)
}

fn star_players() -> NameMatcher {
    players(&[
        ("Lionel Messi", 1001),
        ("Kylian Mbappé", 1002),
        ("Cristiano Ronaldo", 1003),
        ("Robert Lewandowski", 1004),
        ("Erling Haaland", 1005),
        ("Kevin De Bruyne", 1006),
        ("Mohamed Salah", 1007),
        ("Thomas Müller", 1008),
        ("Neymar", 1009),
        ("Luka Modrić", 1010),
    ])
}

// ── players ───────────────────────────────────────────────────────────────

#[test]
fn exact_and_case_and_diacritics() {
    let m = star_players();
    for (query, id) in [
        ("Lionel Messi", 1001),
        ("lionel messi", 1001),
        ("Kylian Mbappe", 1002),
        ("Thomas Muller", 1008),
    ] {
        let found = m.match_player(query, &PlayerQuery::default());
        assert_eq!((found.id, found.confidence), (Some(id), 100.0), "{query}");
        assert_eq!(found.status, MatchStatus::Matched);
    }
}

#[test]
fn fuzzy_abbreviation_word_order_and_partial_name() {
    let m = star_players();
    let found = m.match_player("K. Mbappé", &top(60.0));
    assert_eq!(found.id, Some(1002));
    assert!(found.confidence >= 60.0);
    let found = m.match_player("Ronaldo Cristiano", &top(70.0));
    assert_eq!(found.id, Some(1003));
    assert!(found.confidence >= 70.0);
    let found = m.match_player("Haaland", &top(60.0));
    assert_eq!(found.id, Some(1005));
    assert!(found.confidence >= 60.0);
    assert_eq!(
        m.match_player("John Smith Unknown Player", &top(80.0)).id,
        None
    );
}

#[test]
fn empty_database_is_typed() {
    let found = players(&[]).match_player("Anyone", &PlayerQuery::default());
    assert_eq!(found.id, None);
    assert_eq!(found.confidence, 0.0);
    assert_eq!(found.status, MatchStatus::EmptyDatabase);
}

#[test]
fn exact_name_with_conflicting_age_is_rejected_with_status() {
    let m = with_attrs(&[("Reece James", 126046)], &[(126046, "RB", "", 26)]);
    let found = m.match_player(
        "Reece James",
        &PlayerQuery {
            position: "Defender",
            age: 32,
            ..PlayerQuery::default()
        },
    );
    // The score is kept (100.0); the status says why there is no id.
    assert_eq!(tuple(found.clone()), (None, String::new(), 100.0));
    assert_eq!(found.status, MatchStatus::ExactAgeConflict);
}

#[test]
fn name_variants_reach_95() {
    let m = players(&[("Khéphren Thuram", 1011)]);
    let found = m.match_player("Khéphren Thuram-Ulien", &top(80.0));
    assert_eq!(
        (found.id, found.name.as_str()),
        (Some(1011), "Khéphren Thuram")
    );
    assert!(found.confidence >= 95.0);

    let m = players(&[("Pierre Højbjerg", 1011)]);
    let found = m.match_player("Pierre-Emile Højbjerg", &top(80.0));
    assert_eq!(
        (found.id, found.name.as_str()),
        (Some(1011), "Pierre Højbjerg")
    );
    assert!(found.confidence >= 95.0);

    let m = players(&[("Gabriel Fernando de Jesus", 5001)]);
    let found = m.match_player("Gabriel Jesus", &top(75.0));
    assert_eq!(found.id, Some(5001));
    assert!(found.confidence >= 75.0);
}

#[test]
fn provider_short_name_prefers_suffix_variant_with_metadata() {
    let m = with_attrs(
        &[("Neymar Jr", 1011), ("Neymar Uribe", 1012)],
        &[(1011, "SS", "", 33)],
    );
    let found = m.match_player(
        "Neymar",
        &PlayerQuery {
            threshold: 80.0,
            position: "CAM",
            age: 34,
            ..PlayerQuery::default()
        },
    );
    assert_eq!((found.id, found.name.as_str()), (Some(1011), "Neymar Jr"));
    assert!(found.confidence >= 95.0);
}

fn roster_map(rosters: &[(u32, &[u32])]) -> HashMap<u32, Vec<u32>> {
    rosters.iter().map(|(t, r)| (*t, r.to_vec())).collect()
}

#[test]
fn contextual_disambiguation_picks_the_source_roster_player() {
    let m = players(&[("Danilo Luiz da Silva", 3001), ("Danilo Pereira", 3002)]);
    let map = roster_map(&[(2007, &[3001, 9999]), (2006, &[3002, 8888])]);
    let found = m.match_player(
        "Danilo",
        &PlayerQuery {
            threshold: 70.0,
            from_team_id: Some(2007),
            team_player_map: Some(&map),
            ..Default::default()
        },
    );
    assert_eq!((found.id, found.confidence), (Some(3001), 100.0));
}

#[test]
fn roster_context_rejects_near_name_collision() {
    let m = players(&[("Diney Borges", 58182), ("Diego Torres", 58183)]);
    let map = roster_map(&[(1667, &[58182])]);
    let found = m.match_player(
        "Diego Borges",
        &PlayerQuery {
            threshold: 80.0,
            from_team_id: Some(1667),
            team_player_map: Some(&map),
            ..Default::default()
        },
    );
    assert_eq!((found.id, found.name.as_str()), (None, ""));
    assert!(found.confidence < 90.0);
}

#[test]
fn identical_names_require_context() {
    let m = players(&[("Patrick", 3001), ("Patrick", 3002)]);
    let found = m.match_player("Patrick", &PlayerQuery::default());
    assert_eq!(
        (found.id, found.confidence, found.status),
        (None, 100.0, MatchStatus::AmbiguousExact)
    );

    let map = roster_map(&[(2007, &[3002])]);
    let found = m.match_player(
        "Patrick",
        &PlayerQuery {
            from_team_id: Some(2007),
            team_player_map: Some(&map),
            ..Default::default()
        },
    );
    assert_eq!(tuple(found), (Some(3002), "Patrick".to_owned(), 100.0));
}

#[test]
fn source_roster_has_priority_over_destination() {
    let m = players(&[("Patrick", 3001), ("Patrick", 3002)]);
    let map = roster_map(&[(10, &[3001]), (20, &[3002])]);
    let found = m.match_player(
        "Patrick",
        &PlayerQuery {
            from_team_id: Some(10),
            to_team_id: Some(20),
            team_player_map: Some(&map),
            ..Default::default()
        },
    );
    assert_eq!(tuple(found), (Some(3001), "Patrick".to_owned(), 100.0));
}

#[test]
fn roster_context_does_not_bypass_threshold() {
    let m = players(&[("Alice Brown", 4001)]);
    let map = roster_map(&[(10, &[4001])]);
    let found = m.match_player(
        "Zzzzz Unknown",
        &PlayerQuery {
            threshold: 95.0,
            from_team_id: Some(10),
            team_player_map: Some(&map),
            ..Default::default()
        },
    );
    assert_eq!(found.id, None);
    assert!(found.confidence < 95.0);
}

#[test]
fn goalkeeper_position_separates_same_surname() {
    let m = with_attrs(
        &[("David Raya", 7001), ("David Raya Silva", 7002)],
        &[(7001, "GK", "", 0), (7002, "CF", "", 0)],
    );
    let gk = PlayerQuery {
        position: "GK",
        ..Default::default()
    };
    assert_eq!(m.match_player("David Raya", &gk).id, Some(7001));
    let st = PlayerQuery {
        position: "ST",
        ..Default::default()
    };
    assert_eq!(m.match_player("David Raya", &st).id, Some(7002));
}

#[test]
fn keeper_labels_are_goalkeepers() {
    let m = with_attrs(
        &[("David Raya", 7001), ("David Raya", 7002)],
        &[(7001, "GK", "", 0), (7002, "CF", "", 0)],
    );
    for label in ["Keeper", "Goalkeeper", "Goalie"] {
        let found = m.match_player(
            "David Raya",
            &PlayerQuery {
                position: label,
                ..Default::default()
            },
        );
        assert_eq!(found.id, Some(7001), "{label}");
    }
}

#[test]
fn unknown_age_cannot_lose_to_known_duplicate() {
    let m = with_attrs(
        &[("João Pedro", 9001), ("João Pedro", 9002)],
        &[(9001, "", "", 24)],
    );
    let found = m.match_player(
        "João Pedro",
        &PlayerQuery {
            age: 21,
            ..Default::default()
        },
    );
    assert_eq!((found.id, found.confidence), (None, 100.0));
}

#[test]
fn known_position_line_rejects_other_position_line() {
    let m = with_attrs(
        &[("João Pedro", 9001), ("João Pedro", 9002)],
        &[(9001, "CF", "", 0), (9002, "DMF", "", 0)],
    );
    let found = m.match_player(
        "João Pedro",
        &PlayerQuery {
            position: "Midfielder",
            ..Default::default()
        },
    );
    assert_eq!(tuple(found), (Some(9002), "João Pedro".to_owned(), 100.0));
}

#[test]
fn nationality_age_and_position_disambiguate() {
    let m = with_attrs(
        &[
            ("Gabriel Magalhaes", 8001),
            ("Gabriel Jesus", 8002),
            ("Gabriel Paulista", 8003),
        ],
        &[
            (8001, "CB", "Brazil", 27),
            (8002, "CF", "Brazil", 27),
            (8003, "CB", "Spain", 34),
        ],
    );
    let spain = PlayerQuery {
        position: "CB",
        nationality: "Spain",
        age: 34,
        ..Default::default()
    };
    let found = m.match_player("Gabriel", &spain);
    assert_eq!(
        (found.id, found.name.as_str()),
        (Some(8003), "Gabriel Paulista")
    );
    let brazil = PlayerQuery {
        position: "CB",
        nationality: "Brazil",
        age: 27,
        ..Default::default()
    };
    let found = m.match_player("Gabriel", &brazil);
    assert_eq!(
        (found.id, found.name.as_str()),
        (Some(8001), "Gabriel Magalhaes")
    );
}

#[test]
fn metadata_cannot_drop_stronger_name_match() {
    let m = with_attrs(
        &[("Bruno Silva Santos", 8101), ("Bruno Sousa", 8102)],
        &[(8101, "", "", 25), (8102, "", "", 27)],
    );
    let found = m.match_player(
        "Bruno Silva",
        &PlayerQuery {
            threshold: 70.0,
            age: 27,
            ..Default::default()
        },
    );
    assert_eq!(
        (found.id, found.name.as_str()),
        (Some(8101), "Bruno Silva Santos")
    );
    assert!(found.confidence >= 95.0);
}

#[test]
fn tied_candidates_beyond_ten_are_still_scored() {
    let names: Vec<(String, u32)> = (0..12)
        .map(|i| (format!("Rodrigo Player{i:02}"), 8200 + i))
        .collect();
    let attrs = (0..12)
        .map(|i| {
            let nationality = if i == 11 { "Spain" } else { "Brazil" };
            (
                8200 + i,
                PlayerAttrs {
                    nationality: nationality.to_owned(),
                    ..Default::default()
                },
            )
        })
        .collect();
    let m = NameMatcher::new(names, attrs, Vec::new());
    let found = m.match_player(
        "Rodrigo",
        &PlayerQuery {
            nationality: "Spain",
            ..Default::default()
        },
    );
    assert_eq!(
        (found.id, found.name.as_str(), found.confidence),
        (Some(8211), "Rodrigo Player11", 100.0)
    );
}

#[test]
fn wide_players_match_across_lines() {
    for (provider, pes) in [("LW", "LMF"), ("RW", "RMF"), ("LM", "LWF"), ("RM", "RB")] {
        let m = with_attrs(&[("Alejandro Garnacho", 8301)], &[(8301, pes, "", 0)]);
        let found = m.match_player(
            "Alejandro Garnacho",
            &PlayerQuery {
                position: provider,
                ..Default::default()
            },
        );
        assert_eq!(
            tuple(found),
            (Some(8301), "Alejandro Garnacho".to_owned(), 100.0)
        );
    }
}

// ── nationality substring matching ────────────────────────────────────────

#[test]
fn nationality_substrings_no_longer_decide_identical_names() {
    // "niger" is a substring of "nigeria", but must not pick the Nigerian for a Niger-born query.
    let m = with_attrs(
        &[("Ibrahim Moussa", 1), ("Ibrahim Moussa", 2)],
        &[(1, "", "Nigeria", 0), (2, "", "Niger", 0)],
    );
    let found = m.match_player(
        "Ibrahim Moussa",
        &PlayerQuery {
            nationality: "Niger",
            ..Default::default()
        },
    );
    assert_eq!(found.id, Some(2));
    let found = m.match_player(
        "Ibrahim Moussa",
        &PlayerQuery {
            nationality: "Nigeria",
            ..Default::default()
        },
    );
    assert_eq!(found.id, Some(1));
    // oman ⊂ romania.
    let m = with_attrs(
        &[("Alin Popa", 3), ("Alin Popa", 4)],
        &[(3, "", "Romania", 0), (4, "", "Oman", 0)],
    );
    let found = m.match_player(
        "Alin Popa",
        &PlayerQuery {
            nationality: "Oman",
            ..Default::default()
        },
    );
    assert_eq!(found.id, Some(4));
}

#[test]
fn nationality_evidence_boost_is_exact_country_only() {
    let m = with_attrs(&[("Abel Camara", 5)], &[(5, "", "Guinea-Bissau", 0)]);
    let ctx = |nationality| ScoreContext {
        candidate_pid: Some(5),
        nationality,
        ..Default::default()
    };
    let base = m.score_player("abel camera", "abel camara", &ctx(""));
    assert!(base < 94.0, "{base}");
    assert_eq!(
        m.score_player("abel camera", "abel camara", &ctx("Guinea Bissau")),
        base + 6.0
    );
    // A shared token of a different country is no evidence.
    assert_eq!(
        m.score_player("abel camera", "abel camara", &ctx("Guinea")),
        base
    );
    assert_eq!(
        m.score_player("abel camera", "abel camara", &ctx("Equatorial Guinea")),
        base
    );
}

// ── teams ─────────────────────────────────────────────────────────────────

fn club_matcher() -> NameMatcher {
    teams(&[
        ("Manchester United", 2001),
        ("Manchester City", 2002),
        ("FC Barcelona", 2003),
        ("Real Madrid", 2004),
        ("FC Bayern München", 2005),
        ("Paris Saint-Germain", 2006),
        ("Juventus", 2007),
        ("Inter Milan", 2008),
        ("AC Sparta Praha", 2009),
        ("Atletico Madrid", 2010),
    ])
}

#[test]
fn team_exact_match() {
    let found = club_matcher().match_team("Manchester United", None);
    assert_eq!((found.id, found.confidence), (Some(2001), 100.0));
}

#[test]
fn no_team_aliases_unless_supplied() {
    let m = teams(&[("Manchester United", 100)]);
    let found = m.match_team("Man Utd", None);
    assert_ne!((found.id, found.confidence), (Some(100), 100.0));
}

#[test]
fn supplied_alias_resolves_through_club_affix() {
    let mut m = teams(&[("Juventus FC", 120)]);
    m.set_team_aliases([("Juve".to_owned(), "Juventus".to_owned())]);
    assert_eq!(
        tuple_team(m.match_team("Juve", None)),
        (Some(120), "Juventus FC".to_owned(), 100.0)
    );
}

fn tuple_team(m: TeamMatch) -> (Option<u32>, String, f64) {
    (m.id, m.name, m.confidence)
}

#[test]
fn club_affix_variant_matches_cleaned_name() {
    let m = teams(&[("Lion City Sailors", 71134)]);
    assert_eq!(
        tuple_team(m.match_team("Lion City Sailors FC", None)),
        (Some(71134), "Lion City Sailors".to_owned(), 98.0)
    );
}

#[test]
fn non_club_labels_are_never_fuzzy_matched() {
    let m = club_matcher();
    for label in ["Free Agent", "Without Club", "Retired", ""] {
        let found = m.match_team(label, None);
        assert_eq!(
            (found.id, found.name.as_str(), found.confidence),
            (None, "", 100.0),
            "{label:?}"
        );
        assert_eq!(found.status, MatchStatus::NonClub);
    }
}

#[test]
fn data_derived_alias_normalizes_diacritics() {
    let mut m = club_matcher();
    m.set_team_aliases([("Bayern Munich".to_owned(), "FC Bayern München".to_owned())]);
    let found = m.match_team("Bayern Munich", None);
    assert_eq!((found.id, found.confidence), (Some(2005), 100.0));

    m.set_team_aliases([(
        "Atlético de Madrid".to_owned(),
        "Atletico Madrid".to_owned(),
    )]);
    assert_eq!(
        tuple_team(m.match_team("Atletico de Madrid", None)),
        (Some(2010), "Atletico Madrid".to_owned(), 100.0)
    );
}

#[test]
fn fuzzy_team_and_affix_cleaning() {
    let m = club_matcher();
    let found = m.match_team("Barcelona", Some(60.0));
    assert_eq!(found.id, Some(2003));
    assert!(found.confidence >= 60.0);
    let found = m.match_team("Sparta Praha", Some(80.0));
    assert_eq!(found.id, Some(2009));
    assert!(found.confidence >= 80.0);
    assert_eq!(m.match_team("Nonexistent FC", Some(80.0)).id, None);
}

#[test]
fn ambiguous_affix_cleaned_team_is_not_guessed() {
    let m = teams(&[("FC Barcelona", 2003), ("Barcelona SC", 2658)]);
    let found = m.match_team("Barcelona", Some(60.0));
    assert_eq!((found.id, found.name.as_str()), (None, ""));
    assert!(found.confidence >= 60.0);
    assert_eq!(found.status, MatchStatus::AmbiguousCleaned);
}

#[test]
fn empty_team_database() {
    let found = teams(&[]).match_team("Any Team", None);
    assert_eq!(
        (found.id, found.confidence, found.status),
        (None, 0.0, MatchStatus::EmptyDatabase)
    );
}

#[test]
fn fuzzy_alias_hit_is_not_inflated_to_certainty() {
    // A fuzzy hit on an alias target must not be reported as certain.
    let mut m = teams(&[("Sparta Praha Reserves", 9), ("Atletico Madrid", 10)]);
    m.set_team_aliases([("Atleti".to_owned(), "Atletico Madri".to_owned())]);
    let found = m.match_team("Atleti", None);
    assert_eq!(found.id, Some(10));
    assert!(found.confidence < 100.0, "{}", found.confidence);
}

#[test]
fn team_name_is_the_first_loaded_name_of_the_id() {
    let m = teams(&[("Juventus", 7), ("Juve FC", 7), ("Roma", 8)]);
    assert_eq!(m.team_name(7), Some("Juventus"));
    assert_eq!(m.team_name(99), None);
}
