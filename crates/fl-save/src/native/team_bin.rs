//! `Team.bin`: 1532-byte team records; supplies names for the team keys that
//! `Player.bin` owner references use.

use super::{NativeError, check_record_multiple};
use crate::binstr::read_text;
use std::collections::HashMap;

pub const RECORD_SIZE: usize = 1532;
const TEAM_KEY_OFFSET: usize = 0x08;
const NAME_OFFSET: usize = 0x170;
const NAME_SIZE: usize = 70;
const ABBREVIATION_OFFSET: usize = 0x372;
const ABBREVIATION_SIZE: usize = 10;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TeamBinRecord {
    pub team_key: u32,
    pub name: String,
    pub abbreviation: String,
}

/// Lookup index keyed by team key (first-appearance order, repeats replace in place).
#[derive(Debug, Default)]
pub struct TeamBinDatabase {
    records: Vec<TeamBinRecord>,
    by_key: HashMap<u32, usize>,
}

impl TeamBinDatabase {
    /// Parse raw or WESYS/zlib-compressed `Team.bin` bytes.
    pub fn from_bytes(raw: &[u8]) -> Result<Self, NativeError> {
        let data = crate::wesys::decompress_wesys(raw, "Team.bin")?;
        check_record_multiple("Team.bin", data.len(), RECORD_SIZE)?;

        let mut database = Self::default();
        for record in data.as_chunks::<RECORD_SIZE>().0 {
            let team_key = u32::from_le_bytes([
                record[TEAM_KEY_OFFSET],
                record[TEAM_KEY_OFFSET + 1],
                record[TEAM_KEY_OFFSET + 2],
                record[TEAM_KEY_OFFSET + 3],
            ]);
            let name = read_text(record, NAME_OFFSET, NAME_SIZE);
            if team_key == 0 || name.is_empty() {
                continue;
            }
            let entry = TeamBinRecord {
                team_key,
                name,
                abbreviation: read_text(record, ABBREVIATION_OFFSET, ABBREVIATION_SIZE),
            };
            match database.by_key.get(&team_key) {
                Some(&slot) => database.records[slot] = entry,
                None => {
                    database.by_key.insert(team_key, database.records.len());
                    database.records.push(entry);
                }
            }
        }
        Ok(database)
    }

    pub fn get(&self, team_key: u32) -> Option<&TeamBinRecord> {
        self.by_key.get(&team_key).map(|&slot| &self.records[slot])
    }

    pub fn contains(&self, team_key: u32) -> bool {
        self.by_key.contains_key(&team_key)
    }

    /// Records in first-appearance order.
    pub fn iter(&self) -> std::slice::Iter<'_, TeamBinRecord> {
        self.records.iter()
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

    fn record(key: u32, name: &str, abbreviation: &str) -> Vec<u8> {
        let mut raw = vec![0u8; RECORD_SIZE];
        raw[TEAM_KEY_OFFSET..][..4].copy_from_slice(&key.to_le_bytes());
        raw[NAME_OFFSET..][..name.len()].copy_from_slice(name.as_bytes());
        raw[ABBREVIATION_OFFSET..][..abbreviation.len()].copy_from_slice(abbreviation.as_bytes());
        raw
    }

    #[test]
    fn indexes_named_teams_and_replaces_duplicates() {
        let mut raw = record(5, "Arsenal", "ARS");
        raw.extend(record(0, "No Key", "NK"));
        raw.extend(record(6, "", "EMP"));
        raw.extend(record(7, "  Chelsea ", "CHE"));
        raw.extend(record(5, "Arsenal FC", "AFC"));
        let database = TeamBinDatabase::from_bytes(&raw).unwrap();
        assert_eq!(database.len(), 2);
        assert_eq!(database.get(5).unwrap().name, "Arsenal FC");
        assert_eq!(database.get(5).unwrap().abbreviation, "AFC");
        assert_eq!(database.get(7).unwrap().name, "Chelsea");
        assert!(!database.contains(6) && !database.contains(0));
        let order: Vec<u32> = database.iter().map(|team| team.team_key).collect();
        assert_eq!(order, [5, 7]);
    }

    #[test]
    fn rejects_partial_record() {
        let error = TeamBinDatabase::from_bytes(&vec![1u8; RECORD_SIZE + 1]).unwrap_err();
        assert!(error.to_string().contains("divisible by 1532"), "{error}");
    }
}
