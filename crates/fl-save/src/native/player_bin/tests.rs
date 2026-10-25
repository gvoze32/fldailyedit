use super::*;

fn record(player_id: u32, name: &str, position_index: u8) -> Vec<u8> {
    let mut raw = vec![0u8; RECORD_SIZE];
    raw[PLAYER_ID_OFFSET..][..4].copy_from_slice(&player_id.to_le_bytes());
    raw[NAME_OFFSET..][..name.len()].copy_from_slice(name.as_bytes());
    raw[POSITION_OFFSET] = position_index << 2;
    raw
}

fn put(raw: &mut [u8], offset: usize, value: u32) {
    raw[offset..offset + 4].copy_from_slice(&value.to_le_bytes());
}

#[test]
fn decodes_record_fields() {
    let mut raw = record(4242, "  Test Player ", 5);
    put(&mut raw, YOUTH_TEAM_OFFSET, 11);
    put(&mut raw, OWNER_TEAM_OFFSET, 22);
    put(&mut raw, CONTRACT_UNTIL_OFFSET, 0xF800_0000 | 20_300_630);
    put(&mut raw, LOAN_UNTIL_OFFSET, 20_270_630);
    raw[MARKET_VALUE_OFFSET..][..3].copy_from_slice(&[0x10, 0x27, 0x00]); // 10000 * 100
    raw[CAPS_OFFSET] = 37;
    raw[AGE_OFFSET] = 0xC0 | 14; // upper bits belong to other fields
    raw[PRINT_NAME_OFFSET..][..6].copy_from_slice(b"T. PLY");

    let database = PlayerBinDatabase::from_bytes(&raw).unwrap();
    assert_eq!(database.len(), 1);
    let player = database.get(4242).unwrap();
    assert_eq!(player.name, "Test Player");
    assert_eq!(player.print_name, "T. PLY");
    assert_eq!(player.age, 29);
    assert_eq!(player.position(), Some(Position::Cmf));
    assert_eq!(player.market_value_eur, 1_000_000);
    assert_eq!((player.youth_team_id, player.owner_team_key), (11, 22));
    assert_eq!(
        (player.contract_until, player.loan_until),
        (20_300_630, 20_270_630)
    );
    assert_eq!(player.caps, 37);
    assert!(player.is_on_loan());
    assert!(player.abilities.is_some());
}

#[test]
fn skips_unnamed_and_idless_records_and_replaces_duplicates_in_place() {
    let mut raw = record(1, "First", 0);
    raw.extend(record(0, "No Id", 0));
    raw.extend(record(2, "", 0));
    raw.extend(record(3, "Third", 1));
    raw.extend(record(1, "First Again", 2));
    let database = PlayerBinDatabase::from_bytes(&raw).unwrap();
    let order: Vec<(u32, &str)> = database
        .iter()
        .map(|r| (r.player_id, r.name.as_str()))
        .collect();
    assert_eq!(order, [(1, "First Again"), (3, "Third")]);
    assert!(database.contains(3) && !database.contains(2) && !database.contains(0));
}

#[test]
fn unknown_position_has_no_abilities() {
    let database = PlayerBinDatabase::from_bytes(&record(7, "Odd", 15)).unwrap();
    let player = database.get(7).unwrap();
    assert_eq!(player.position_index, 15);
    assert_eq!(player.position(), None);
    assert!(player.abilities.is_none());
    assert_eq!(player.overall(), 0);
    let native = database.native_player(7).unwrap();
    assert!(native.position.is_none() && native.abilities.is_none());
}

#[test]
fn loan_needs_owner_and_expiry() {
    let mut raw = record(9, "Loanee", 0);
    put(&mut raw, LOAN_UNTIL_OFFSET, 20_270_630);
    assert!(
        !PlayerBinDatabase::from_bytes(&raw)
            .unwrap()
            .get(9)
            .unwrap()
            .is_on_loan()
    );
}

#[test]
fn reads_wesys_wrapped_database_and_rejects_partial_records() {
    use flate2::{Compression, write::ZlibEncoder};
    use std::io::Write;
    let plain = record(5, "Wrapped", 3);
    let mut encoder = ZlibEncoder::new(Vec::new(), Compression::default());
    encoder.write_all(&plain).unwrap();
    let compressed = encoder.finish().unwrap();
    let mut wrapped = vec![0u8, 0, 0];
    wrapped.extend_from_slice(b"WESYS");
    wrapped.extend_from_slice(&(compressed.len() as u32).to_le_bytes());
    wrapped.extend_from_slice(&(plain.len() as u32).to_le_bytes());
    wrapped.extend_from_slice(&compressed);
    assert_eq!(
        PlayerBinDatabase::from_bytes(&wrapped)
            .unwrap()
            .get(5)
            .unwrap()
            .name,
        "Wrapped"
    );

    let error = PlayerBinDatabase::from_bytes(&plain[..RECORD_SIZE - 1]).unwrap_err();
    assert!(error.to_string().contains("divisible by 312"), "{error}");
}

#[test]
fn native_players_view_matches_record() {
    let database = PlayerBinDatabase::from_bytes(&record(8, "Viewed", 4)).unwrap();
    let native = database.native_player(8).unwrap();
    assert_eq!(
        (native.player_id, native.name, native.age),
        (8, "Viewed", 15)
    );
    assert_eq!(native.position, Some(Position::Dmf));
    assert!(database.native_player(99).is_none());
}
