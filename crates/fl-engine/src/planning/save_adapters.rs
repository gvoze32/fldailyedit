//! The planning ports implemented by the engine's concrete types.

use std::collections::HashSet;

use fl_save::edit_file::EditFile;
use fl_save::roster::{OverflowExplanation, OverflowQuery};

use super::ports::{
    ClubIdentity, OverflowSelector, PlayerHit, PlayerMatcher, PlayerQuery, PlayerRecord,
    Resolution, RosterReader, ScoreContext, TeamHit, TeamMatcher,
};
use crate::club_identity::ClubIdentityIndex;
use crate::matcher::NameMatcher;

impl TeamMatcher for NameMatcher {
    fn match_team(&self, name: &str, threshold: Option<f64>) -> TeamHit {
        let found = NameMatcher::match_team(self, name, threshold);
        TeamHit {
            id: found.id,
            name: found.name,
            confidence: found.confidence,
        }
    }

    fn team_name(&self, team_id: u32) -> &str {
        NameMatcher::team_name(self, team_id).unwrap_or("")
    }
}

impl PlayerMatcher for NameMatcher {
    fn match_player(&self, name: &str, query: &PlayerQuery<'_>) -> PlayerHit {
        let found = NameMatcher::match_player(self, name, query);
        PlayerHit {
            id: found.id,
            name: found.name,
            confidence: found.confidence,
        }
    }

    fn exact_player_candidates(&self, folded_name: &str) -> &[PlayerRecord] {
        NameMatcher::exact_player_candidates(self, folded_name)
    }

    fn is_player_age_compatible(&self, player_id: u32, age: u32) -> bool {
        NameMatcher::is_player_age_compatible(self, player_id, age)
    }

    fn is_player_metadata_compatible(&self, player_id: u32, position: &str, age: u32) -> bool {
        NameMatcher::is_player_metadata_compatible(self, player_id, position, age)
    }

    fn score_player(&self, query_norm: &str, candidate_norm: &str, ctx: &ScoreContext<'_>) -> f64 {
        NameMatcher::score_player(self, query_norm, candidate_norm, ctx)
    }

    fn player_names(&self, player_id: u32) -> &[(String, String)] {
        NameMatcher::player_names(self, player_id)
    }

    fn player_position(&self, player_id: u32) -> &str {
        NameMatcher::player_position(self, player_id)
    }
}

impl ClubIdentity for ClubIdentityIndex {
    fn pes_for_fotmob(&self, fotmob_id: i64) -> Option<u32> {
        ClubIdentityIndex::pes_for_fotmob(self, fotmob_id)
    }

    fn fotmob_for_pes(&self, team_id: u32) -> Option<i64> {
        ClubIdentityIndex::fotmob_for_pes(self, team_id)
    }

    fn resolve_name(&self, name: &str) -> Resolution {
        ClubIdentityIndex::resolve_name(self, name)
    }

    fn bindings(&self) -> Vec<(i64, u32)> {
        self.entries()
            .into_iter()
            .map(|entry| (entry.fotmob_id, entry.pes_team_id))
            .collect()
    }
}

impl OverflowSelector for EditFile {
    fn find_candidate(
        &self,
        team_id: u32,
        incoming: u32,
        roster: &[u32],
        protected: &HashSet<u32>,
    ) -> Option<u32> {
        self.find_overflow_release_candidate(
            team_id,
            OverflowQuery {
                exclude_player_id: Some(incoming),
                roster_player_ids: Some(roster),
                protected_player_ids: Some(protected),
            },
        )
        .map(|candidate| candidate.player_id)
        .filter(|&player| player != 0)
    }

    fn describe(
        &self,
        team_id: u32,
        player_id: u32,
        roster: &[u32],
        protected: &HashSet<u32>,
    ) -> Option<OverflowExplanation> {
        self.describe_overflow_release_candidate(
            team_id,
            player_id,
            OverflowQuery {
                exclude_player_id: None,
                roster_player_ids: Some(roster),
                protected_player_ids: Some(protected),
            },
        )
    }
}

impl RosterReader for EditFile {
    fn roster(&self, team_id: u32) -> Option<(Vec<u32>, Vec<u32>)> {
        let team = EditFile::roster(self, team_id)?;
        Some((
            team.player_ids.to_vec(),
            team.shirt_numbers.iter().map(|&n| u32::from(n)).collect(),
        ))
    }
}
