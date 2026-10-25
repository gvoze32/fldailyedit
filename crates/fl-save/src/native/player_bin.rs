//! `Player.bin`: 312-byte player records of the game database.

use super::{NativeError, check_record_multiple};
use crate::binstr::read_text;
use crate::model::{NativeCareer, NativePlayer, NativePlayers, Position};
use crate::rating::{PlayerAbilities, decode_playerbin_record};
use std::collections::HashMap;

pub const RECORD_SIZE: usize = 312;
const YOUTH_TEAM_OFFSET: usize = 0x00;
const OWNER_TEAM_OFFSET: usize = 0x04;
const PLAYER_ID_OFFSET: usize = 0x08;
const CONTRACT_UNTIL_OFFSET: usize = 0x0C;
const LOAN_UNTIL_OFFSET: usize = 0x10;
const MARKET_VALUE_OFFSET: usize = 0x14;
const CAPS_OFFSET: usize = 0x17;
const AGE_OFFSET: usize = 0x33;
const POSITION_OFFSET: usize = 0x36;
const NAME_OFFSET: usize = 0x44;
const PRINT_NAME_OFFSET: usize = 0x81;
const NAME_SIZE: usize = 61;
/// Contract / loan expiry are 27-bit values packed into a u32.
const DATE_MASK: u32 = 0x7FF_FFFF;

#[derive(Debug, Clone, PartialEq)]
pub struct PlayerBinRecord {
    pub player_id: u32,
    pub name: String,
    pub print_name: String,
    /// Age in years (stored as `age - 15` in 6 bits).
    pub age: u32,
    /// Raw 4-bit registered-position index; see [`PlayerBinRecord::position`].
    pub position_index: u8,
    pub market_value_eur: u32,
    pub youth_team_id: u32,
    pub owner_team_key: u32,
    pub contract_until: u32,
    pub loan_until: u32,
    pub caps: u8,
    /// Decoded abilities; `None` when the registered position is unknown.
    pub abilities: Option<PlayerAbilities>,
}

impl PlayerBinRecord {
    /// Registered position, `None` for an index outside the 13 known positions.
    pub fn position(&self) -> Option<Position> {
        Position::from_index(self.position_index)
    }

    /// Native overall at the registered position; 0 when undecodable.
    pub fn overall(&self) -> i32 {
        self.abilities.as_ref().map_or(0, PlayerAbilities::overall)
    }

    pub fn is_on_loan(&self) -> bool {
        self.loan_until > 0 && self.owner_team_key > 0
    }
}

/// Lookup index keyed by player ID. Iteration follows first appearance in the
/// file; a repeated ID replaces the earlier record in place.
#[derive(Debug, Default)]
pub struct PlayerBinDatabase {
    records: Vec<PlayerBinRecord>,
    by_id: HashMap<u32, usize>,
}

impl PlayerBinDatabase {
    /// Parse raw or WESYS/zlib-compressed `Player.bin` bytes.
    pub fn from_bytes(raw: &[u8]) -> Result<Self, NativeError> {
        let data = crate::wesys::decompress_wesys(raw, "Player.bin")?;
        check_record_multiple("Player.bin", data.len(), RECORD_SIZE)?;

        let mut database = Self::default();
        for (index, record) in data.as_chunks::<RECORD_SIZE>().0.iter().enumerate() {
            let player_id = le_u32(record, PLAYER_ID_OFFSET);
            if player_id == 0 {
                continue;
            }
            let name = read_text(record, NAME_OFFSET, NAME_SIZE);
            if name.is_empty() {
                continue;
            }
            let position_index = (record[POSITION_OFFSET] >> 2) & 0x0F;
            let abilities = match Position::from_index(position_index) {
                Some(_) => Some(decode_playerbin_record(&data, index * RECORD_SIZE)?),
                None => None,
            };
            database.insert(PlayerBinRecord {
                player_id,
                name,
                print_name: read_text(record, PRINT_NAME_OFFSET, NAME_SIZE),
                age: u32::from(record[AGE_OFFSET] & 0x3F) + 15,
                position_index,
                market_value_eur: u32::from_le_bytes([
                    record[MARKET_VALUE_OFFSET],
                    record[MARKET_VALUE_OFFSET + 1],
                    record[MARKET_VALUE_OFFSET + 2],
                    0,
                ]) * 100,
                youth_team_id: le_u32(record, YOUTH_TEAM_OFFSET),
                owner_team_key: le_u32(record, OWNER_TEAM_OFFSET),
                contract_until: le_u32(record, CONTRACT_UNTIL_OFFSET) & DATE_MASK,
                loan_until: le_u32(record, LOAN_UNTIL_OFFSET) & DATE_MASK,
                caps: record[CAPS_OFFSET],
                abilities,
            });
        }
        Ok(database)
    }

    fn insert(&mut self, record: PlayerBinRecord) {
        match self.by_id.get(&record.player_id) {
            Some(&slot) => self.records[slot] = record,
            None => {
                self.by_id.insert(record.player_id, self.records.len());
                self.records.push(record);
            }
        }
    }

    pub fn get(&self, player_id: u32) -> Option<&PlayerBinRecord> {
        self.by_id.get(&player_id).map(|&slot| &self.records[slot])
    }

    pub fn contains(&self, player_id: u32) -> bool {
        self.by_id.contains_key(&player_id)
    }

    /// Records in first-appearance order.
    pub fn iter(&self) -> std::slice::Iter<'_, PlayerBinRecord> {
        self.records.iter()
    }

    pub fn len(&self) -> usize {
        self.records.len()
    }

    pub fn is_empty(&self) -> bool {
        self.records.is_empty()
    }
}

impl NativePlayers for PlayerBinDatabase {
    fn native_player(&self, id: u32) -> Option<NativePlayer<'_>> {
        self.get(id).map(|record| NativePlayer {
            player_id: record.player_id,
            name: &record.name,
            print_name: &record.print_name,
            position: record.position(),
            age: record.age,
            abilities: record.abilities.as_ref(),
        })
    }

    fn native_career(&self, id: u32) -> Option<NativeCareer> {
        self.get(id).map(|record| NativeCareer {
            caps: u32::from(record.caps),
            market_value_eur: record.market_value_eur,
        })
    }
}

fn le_u32(record: &[u8], offset: usize) -> u32 {
    u32::from_le_bytes([
        record[offset],
        record[offset + 1],
        record[offset + 2],
        record[offset + 3],
    ])
}

#[cfg(test)]
mod tests;
