use super::testing::{Entry, build_cpk};
use super::*;
use std::io::Cursor;

fn open(archive: Vec<u8>) -> Cpk<Cursor<Vec<u8>>> {
    Cpk::from_reader(Cursor::new(archive)).unwrap()
}

fn sample() -> Vec<Entry<'static>> {
    vec![
        Entry {
            dir: "common/etc/pesdb",
            name: "Player.bin",
            payload: b"player-bytes",
        },
        Entry {
            dir: "common/etc/pesdb",
            name: "Team.bin",
            payload: b"team",
        },
        Entry {
            dir: "other",
            name: "Team.bin",
            payload: b"other-team",
        },
        Entry {
            dir: "",
            name: "root.txt",
            payload: b"root",
        },
    ]
}

#[test]
fn lists_and_reads_by_exact_path() {
    let mut cpk = open(build_cpk(&sample(), None, false));
    assert_eq!(
        cpk.list_files(),
        [
            "common/etc/pesdb/Player.bin",
            "common/etc/pesdb/Team.bin",
            "other/Team.bin",
            "root.txt"
        ]
    );
    assert_eq!(
        cpk.read_file("common/etc/pesdb/Player.bin").unwrap(),
        b"player-bytes"
    );
    assert_eq!(
        cpk.read_file("\\common\\etc\\pesdb\\Team.bin").unwrap(),
        b"team"
    );
    assert_eq!(cpk.read_file("root.txt").unwrap(), b"root");
}

#[test]
fn resolves_unique_trailing_names_and_rejects_ambiguity() {
    let mut cpk = open(build_cpk(&sample(), None, false));
    assert_eq!(cpk.read_file("Player.bin").unwrap(), b"player-bytes");
    assert_eq!(cpk.read_file("pesdb/Player.bin").unwrap(), b"player-bytes");
    let ambiguous = cpk.read_file("Team.bin").unwrap_err();
    assert!(
        matches!(&ambiguous, CpkError::Format(m) if m.contains("ambiguous")),
        "{ambiguous}"
    );
    assert!(matches!(
        cpk.read_file("Missing.bin"),
        Err(CpkError::NotFound(_))
    ));
}

#[test]
fn duplicate_exact_paths_are_rejected() {
    let entries = vec![
        Entry {
            dir: "a",
            name: "x.bin",
            payload: b"1",
        },
        Entry {
            dir: "a",
            name: "x.bin",
            payload: b"2",
        },
    ];
    let mut cpk = open(build_cpk(&entries, None, false));
    assert!(
        matches!(cpk.read_file("a/x.bin"), Err(CpkError::Format(m)) if m.contains("duplicate"))
    );
}

#[test]
fn finds_toc_marker_when_offset_is_unset() {
    let mut cpk = open(build_cpk(&sample(), Some(u64::MAX), false));
    assert_eq!(cpk.read_file("root.txt").unwrap(), b"root");
}

#[test]
fn reads_protected_mode5_toc() {
    let mut cpk = open(build_cpk(&sample(), None, true));
    assert_eq!(cpk.list_files().len(), 4);
    assert_eq!(
        cpk.read_file("common/etc/pesdb/Player.bin").unwrap(),
        b"player-bytes"
    );
}

#[test]
fn payload_bases_fall_back_and_bounds_are_enforced() {
    let cpk = open(build_cpk(&sample(), None, false));
    let len = cpk.len;
    // Offset relative to the content base fits: first candidate wins.
    assert_eq!(cpk.payload_start(26, 4).unwrap(), 0x800 + 26);
    // Too far for any base.
    assert!(matches!(cpk.payload_start(len, 1), Err(CpkError::Format(m)) if m.contains("exceed")));
    assert!(cpk.payload_start(u64::MAX, 1).is_err());
    assert!(cpk.payload_start(0, u64::MAX).is_err());
}

#[test]
fn rejects_garbage_archives() {
    assert!(Cpk::from_reader(Cursor::new(vec![0u8; 10])).is_err());
    assert!(Cpk::from_reader(Cursor::new(vec![0u8; 64])).is_err());
    // TocOffset beyond the end of the archive.
    assert!(Cpk::from_reader(Cursor::new(build_cpk(&sample(), Some(5_000_000), false))).is_err());
}

#[test]
fn file_marker_scan_crosses_chunk_boundaries() {
    let mut data = vec![0u8; (1 << 20) - 2];
    data.extend_from_slice(b"TOC ");
    data.extend_from_slice(&[0u8; 100]);
    let len = data.len() as u64;
    let found = find_marker(&mut Cursor::new(data), len, b"TOC ").unwrap();
    assert_eq!(found, Some((1 << 20) - 2));
    assert_eq!(
        find_marker(&mut Cursor::new(vec![1u8; 5000]), 5000, b"TOC ").unwrap(),
        None
    );
}
