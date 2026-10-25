//! `PlayerAssignment.bin`: 16-byte roster ownership rows, indexed by player
//! without merging variants.

use super::{NativeError, check_record_multiple};
use std::collections::HashMap;

pub const RECORD_SIZE: usize = 16;

/// One assignment row; `record_key` and `auxiliary` are opaque words.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PlayerAssignmentRecord {
    pub record_key: u32,
    pub player_id: u32,
    pub team_key: u32,
    pub auxiliary: u32,
}

#[derive(Debug, Default)]
pub struct PlayerAssignmentDatabase {
    records: Vec<PlayerAssignmentRecord>,
    /// Row indices per non-zero player ID, in file order.
    by_player: HashMap<u32, Vec<u32>>,
}

impl PlayerAssignmentDatabase {
    /// Parse raw or WESYS/zlib-compressed `PlayerAssignment.bin` bytes.
    pub fn from_bytes(raw: &[u8]) -> Result<Self, NativeError> {
        let data = crate::wesys::decompress_wesys(raw, "PlayerAssignment.bin")?;
        check_record_multiple("PlayerAssignment.bin", data.len(), RECORD_SIZE)?;

        let word = |row: &[u8], at: usize| {
            u32::from_le_bytes([row[at], row[at + 1], row[at + 2], row[at + 3]])
        };
        let records: Vec<PlayerAssignmentRecord> = data
            .as_chunks::<RECORD_SIZE>()
            .0
            .iter()
            .map(|row| PlayerAssignmentRecord {
                record_key: word(row, 0),
                player_id: word(row, 4),
                team_key: word(row, 8),
                auxiliary: word(row, 12),
            })
            .collect();
        let mut by_player: HashMap<u32, Vec<u32>> = HashMap::new();
        for (index, record) in records.iter().enumerate() {
            if record.player_id != 0 {
                by_player
                    .entry(record.player_id)
                    .or_default()
                    .push(index as u32);
            }
        }
        Ok(Self { records, by_player })
    }

    /// Every row, in file order.
    pub fn records(&self) -> &[PlayerAssignmentRecord] {
        &self.records
    }

    /// Number of distinct non-zero player IDs.
    pub fn player_count(&self) -> usize {
        self.by_player.len()
    }

    /// Rows of one player in file order.
    pub fn get(&self, player_id: u32) -> impl Iterator<Item = &PlayerAssignmentRecord> + '_ {
        self.by_player
            .get(&player_id)
            .into_iter()
            .flatten()
            .map(|&index| &self.records[index as usize])
    }

    /// Distinct team keys of one player in file order.
    pub fn team_keys_for(&self, player_id: u32) -> Vec<u32> {
        let mut keys: Vec<u32> = Vec::new();
        for record in self.get(player_id) {
            if !keys.contains(&record.team_key) {
                keys.push(record.team_key);
            }
        }
        keys
    }

    pub fn len(&self) -> usize {
        self.records.len()
    }

    pub fn is_empty(&self) -> bool {
        self.records.is_empty()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn row(key: u32, player: u32, team: u32, aux: u32) -> Vec<u8> {
        [key, player, team, aux]
            .iter()
            .flat_map(|w| w.to_le_bytes())
            .collect()
    }

    #[test]
    fn indexes_rows_by_player_in_file_order() {
        let mut raw = row(1, 10, 100, 7);
        raw.extend(row(2, 0, 100, 0));
        raw.extend(row(3, 10, 200, 0));
        raw.extend(row(4, 10, 100, 1));
        raw.extend(row(5, 11, 100, 2));
        let database = PlayerAssignmentDatabase::from_bytes(&raw).unwrap();
        assert_eq!(database.len(), 5);
        assert_eq!(database.player_count(), 2);
        assert_eq!(database.team_keys_for(10), [100, 200]);
        assert_eq!(
            database.get(10).map(|r| r.record_key).collect::<Vec<_>>(),
            [1, 3, 4]
        );
        assert_eq!(database.get(99).count(), 0);
        assert!(database.team_keys_for(0).is_empty());
    }

    #[test]
    fn rejects_partial_record() {
        assert!(PlayerAssignmentDatabase::from_bytes(&[0u8; 17]).is_err());
    }
}
