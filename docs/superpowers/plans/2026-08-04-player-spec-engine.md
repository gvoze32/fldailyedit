# Player Spec Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the automatic curated-player pilot with revision-scoped, per-player create/update specs applied only by an explicit CLI command.

**Architecture:** Keep the proven PES binary codec and append operation, but decouple them from `CuratedPlayer`. A new `editor.player_spec` module validates one JSON file per player, enforces bundled-base lifecycle rules, and applies create/update operations atomically. `run` remains transfer-only; GitHub sync workflows invoke `players apply` explicitly after transfers.

**Tech Stack:** Python 3.10+, standard-library dataclasses/JSON/hashlib/pathlib, existing aiohttp and pytest dependencies, existing pesXdecrypter binaries.

## Global Constraints

- Do not reset the current working tree: it contains the uncommitted generic codec and append primitives that this plan intentionally retains.
- Remove `data/curated_players.json`, `config.CURATED_PLAYERS_FILE`, and implicit player creation from `cmd_run`.
- Keep one JSON file per player under `players/<canonical-slug>.json`.
- Support only `schema_version: 1` and `operation: create | update`.
- Only active specs whose `applies_to` contains the verified base revision may mutate a save.
- Never infer PES abilities from SortitoutSI CA or Football Manager attributes.
- Never auto-release a rostered player to make room for a create spec.
- Update specs must use explicit `{from, to}` values and preserve all unrequested bits.
- Player-spec mutations are per-player atomic; final output requires integrity validation plus encrypt/decrypt round trip.
- Dastan Satpayev remains the create example; Marco Palestra remains the explicit update example.

---

### Task 1: Base Revision Manifest and Player Spec Loader

**Files:**
- Create: `data/base_manifest.json`
- Create: `editor/player_spec.py`
- Create: `tests/test_player_specs.py`
- Modify: `config.py:31-40`

**Interfaces:**
- Produces: `BaseManifest(revision: str, sha256: str)`, `FieldPatch(current: int, target: int)`, `PlayerIdentity`, `CreatePlayerData`, `PlayerSpec`, `PlayerSpecError`.
- Produces: `load_base_manifest(path: str | Path | None = None) -> BaseManifest`.
- Produces: `load_player_specs(directory: str | Path | None = None) -> tuple[PlayerSpec, ...]`.
- Produces: `player_slug(name: str) -> str` and `validate_spec_set(specs: tuple[PlayerSpec, ...]) -> None`.
- Consumes: existing `ABILITY_FIELDS`, `POSITION_NAMES`, `PLAYER_SKILL_FIELDS`, and `COM_STYLE_FIELDS` from `editor.player_codec`.

- [ ] **Step 1: Write failing manifest and schema tests**

Create `tests/test_player_specs.py` with focused contracts:

```python
import hashlib
import json

import pytest


def test_base_manifest_matches_bundled_edit():
    from editor.player_spec import load_base_manifest

    manifest = load_base_manifest()
    digest = hashlib.sha256()
    with open("base/EDIT00000000", "rb") as bundled:
        for chunk in iter(lambda: bundled.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    assert manifest.revision == "fl26-u2.2-national-squads"
    assert manifest.sha256 == actual


def test_load_specs_rejects_filename_identity_and_duplicate_ids(tmp_path):
    from editor.player_spec import PlayerSpecError, load_player_specs

    payload = {
        "schema_version": 1,
        "operation": "update",
        "lifecycle": {"status": "active"},
        "applies_to": ["fl26-u2.2-national-squads"],
        "identity": {
            "name": "Marco Palestra",
            "aliases": ["Marco Palestra"],
            "pes_id": 162196,
            "sortitoutsi_id": 2000136198,
        },
        "evidence": {
            "profile_url": "https://sortitoutsi.net/football-manager-data-update/person/2000136198",
            "proof_urls": ["https://sortitoutsi.net/football-manager-data-update/attributes/submission/526121"],
            "effective_date": "2026-07-25",
            "reason": "Approved attribute submission",
        },
        "pes": {"abilities": {"speed": {"from": 77, "to": 80}}},
    }
    (tmp_path / "wrong-name.json").write_text(json.dumps(payload))
    with pytest.raises(PlayerSpecError, match="filename"):
        load_player_specs(tmp_path)


def test_update_patch_requires_distinct_in_range_values(tmp_path):
    from editor.player_spec import PlayerSpecError, load_player_specs

    # Use a helper local to this test module to write a valid Marco spec, then
    # replace speed with the invalid no-op patch below.
    payload = valid_marco_payload()
    payload["pes"]["abilities"]["speed"] = {"from": 100, "to": 100}
    (tmp_path / "marco-palestra.json").write_text(json.dumps(payload))
    with pytest.raises(PlayerSpecError, match="speed"):
        load_player_specs(tmp_path)
```

Add fixtures `valid_marco_payload()` and `valid_dastan_payload()` containing the exact schemas later checked into `players/`; do not use truncated or placeholder fields.

- [ ] **Step 2: Run the new tests and confirm the missing module failure**

Run: `.venv/bin/python -m pytest tests/test_player_specs.py -q`

Expected: collection fails with `ModuleNotFoundError: No module named 'editor.player_spec'`.

- [ ] **Step 3: Add the checked-in base manifest and config paths**

Create `data/base_manifest.json`:

```json
{
  "revision": "fl26-u2.2-national-squads",
  "sha256": "498b3bdf70084991a05a6d765405ff17e3f89af87869dceccb29fb2a06a8f10c"
}
```

Add to `config.py`:

```python
BASE_MANIFEST_FILE = DATA_DIR / "base_manifest.json"
PLAYER_SPECS_DIR = PROJECT_ROOT / "players"
```

- [ ] **Step 4: Implement strict data models and JSON loading**

In `editor/player_spec.py`, define frozen slot dataclasses and literal validation without adding a JSON-schema dependency:

```python
@dataclass(frozen=True, slots=True)
class BaseManifest:
    revision: str
    sha256: str


@dataclass(frozen=True, slots=True)
class FieldPatch:
    current: int
    target: int


@dataclass(frozen=True, slots=True)
class PlayerIdentity:
    name: str
    print_name: str | None
    aliases: tuple[str, ...]
    pes_id: int
    sortitoutsi_id: int


@dataclass(frozen=True, slots=True)
class Evidence:
    profile_url: str
    proof_urls: tuple[str, ...]
    effective_date: date
    reason: str


@dataclass(frozen=True, slots=True)
class CreatePlayerData:
    player_id: int
    name: str
    print_name: str
    team_id: int
    team_name: str
    preferred_shirt_number: int | None
    nationality_id: int
    age: int
    height: int
    weight: int
    registered_position: str
    playing_style: int
    strong_foot: int
    weak_foot_usage: int
    weak_foot_accuracy: int
    form: int
    injury_resistance: int
    position_proficiency: Mapping[str, int]
    abilities: Mapping[str, int]
    player_skills: tuple[str, ...]
    com_styles: tuple[str, ...]
    skin_color: int
    iris_color: int


@dataclass(frozen=True, slots=True)
class PlayerSpec:
    path: Path
    schema_version: int
    operation: str
    lifecycle_status: str
    lifecycle_reason: str
    superseded_by: str | None
    applies_to: tuple[str, ...]
    identity: PlayerIdentity
    evidence: Evidence
    create: CreatePlayerData | None
    patches: Mapping[str, FieldPatch]
```

Implement `player_slug` with Unicode NFKD normalization, lowercase ASCII tokens, and single hyphens. Reject unknown top-level keys, unsupported lifecycle values, empty/duplicate aliases, non-HTTPS evidence URLs, malformed SHA-256, unknown PES fields, values outside their bit width, and ability values outside `40..99`.

`load_player_specs` must glob only `*.json`, sort by filename, validate `path.stem == player_slug(identity.name)`, then call `validate_spec_set` to reject duplicate PES IDs, SortitoutSI IDs, and normalized aliases across files.

- [ ] **Step 5: Run loader tests**

Run: `.venv/bin/python -m pytest tests/test_player_specs.py -q`

Expected: all Task 1 tests pass.

- [ ] **Step 6: Commit Task 1**

```bash
git add config.py data/base_manifest.json editor/player_spec.py tests/test_player_specs.py
git commit -m "feat(players): add revision-scoped specs"
```

---

### Task 2: Generic Create Serializer and Atomic Create Operation

**Files:**
- Modify: `editor/player_codec.py:1-20,379-445`
- Modify: `editor/editfile.py:309-326,664-705`
- Modify: `editor/player_spec.py`
- Modify: `tests/test_player_specs.py`
- Modify: `tests/test_player_codec.py`
- Remove after migration: `tests/test_curated_players.py`

**Interfaces:**
- Consumes: `CreatePlayerData` and `PlayerSpec` from Task 1.
- Produces: `CreatedPlayerRecord` protocol in `editor.player_codec`.
- Produces: `serialize_created_player(player: CreatedPlayerRecord) -> tuple[bytes, bytes]`.
- Produces: `SpecResult(pes_id: int, name: str, status: str, reason: str)`.
- Produces: `assess_create(edit_file: EditFile, spec: PlayerSpec, all_players: Mapping[int, PlayerInfo]) -> SpecResult`.
- Produces: `apply_create(edit_file: EditFile, spec: PlayerSpec, all_players: Mapping[int, PlayerInfo]) -> SpecResult`.

- [ ] **Step 1: Move create contracts out of curated-player tests**

Port the useful tests from `tests/test_curated_players.py` into `tests/test_player_specs.py` and remove references to `load_curated_players`:

```python
def test_create_serializer_builds_linked_player_and_appearance_records():
    from editor.player_codec import decode_player_entry, serialize_created_player
    from editor.player_spec import load_player_specs

    spec = next(s for s in load_player_specs("tests/fixtures/player_specs") if s.operation == "create")
    player_entry, appearance_entry = serialize_created_player(spec.create)
    profile = decode_player_entry(player_entry)

    assert int.from_bytes(player_entry[:4], "little") == spec.identity.pes_id
    assert int.from_bytes(appearance_entry[:4], "little") == spec.identity.pes_id
    assert profile.abilities == spec.create.abilities


def test_full_roster_returns_waiting_without_mutation():
    edit_file = make_player_spec_edit_file(roster_size=40)
    before = bytes(edit_file._data)
    spec = dastan_spec()

    result = apply_create(edit_file, spec, {})

    assert result.status == "waiting"
    assert bytes(edit_file._data) == before
```

Keep tests for linked roster/game-plan registration, rerun idempotency, rollback when roster insertion fails, unused IDs below existing created IDs, and generic appearance linkage.

- [ ] **Step 2: Run the migrated tests and confirm interface failures**

Run: `.venv/bin/python -m pytest tests/test_player_specs.py tests/test_player_codec.py -q`

Expected: failures because `CreatePlayerData` is not yet accepted by `serialize_created_player` and `apply_create` is missing.

- [ ] **Step 3: Decouple the serializer from `CuratedPlayer`**

Replace the type-only `CuratedPlayer` import with a protocol containing every field read by the serializer:

```python
class CreatedPlayerRecord(Protocol):
    player_id: int
    name: str
    print_name: str
    nationality_id: int
    age: int
    height: int
    weight: int
    registered_position: str
    playing_style: int
    strong_foot: int
    weak_foot_usage: int
    weak_foot_accuracy: int
    form: int
    injury_resistance: int
    position_proficiency: Mapping[str, int]
    abilities: Mapping[str, int]
    player_skills: tuple[str, ...]
    com_styles: tuple[str, ...]
    skin_color: int
    iris_color: int
```

Make `CreatePlayerData` expose `player_id`, `name`, and `print_name` directly or through exact properties so it satisfies the protocol without allocation or conversion.

- [ ] **Step 4: Implement generic create assessment and rollback**

Move identity normalization and create safety logic from `editor.curated_player` into `editor.player_spec`. `assess_create` must check lifecycle compatibility before identity or roster access, reject an ID collision with a different normalized identity, return `already_applied` for a matching existing player, validate exact destination team ID/name, and return `waiting` for a full roster.

`apply_create` must snapshot `bytes(edit_file._data)`, serialize, call `append_created_player`, then call `edit_file.add_player(spec.identity.pes_id, spec.create.team_id, preferred_shirt_number=spec.create.preferred_shirt_number, position=spec.create.registered_position, allow_overflow_release=False)`. Restore `_data`, header counts, and offsets on every exception. It must never auto-release another player.

- [ ] **Step 5: Run create and codec tests**

Run: `.venv/bin/python -m pytest tests/test_player_specs.py tests/test_player_codec.py -q`

Expected: all create, codec, atomicity, and idempotency tests pass.

- [ ] **Step 6: Remove obsolete curated-only tests**

Delete `tests/test_curated_players.py` only after every generic binary contract is represented in `tests/test_player_specs.py` or `tests/test_player_codec.py`.

- [ ] **Step 7: Commit Task 2**

```bash
git add editor/player_codec.py editor/editfile.py editor/player_spec.py tests/test_player_specs.py tests/test_player_codec.py
git rm tests/test_curated_players.py
git commit -m "refactor(players): generalize create engine"
```

---

### Task 3: Explicit Existing-Player Patch Engine

**Files:**
- Modify: `editor/editfile.py:309-326`
- Modify: `editor/player_spec.py`
- Modify: `tests/test_player_specs.py`
- Modify: `tests/test_player_codec.py`

**Interfaces:**
- Consumes: `FieldPatch`, `PlayerSpec`, `patch_player_entry`, and `decode_player_entry`.
- Produces: `EditFile.get_edited_player_entry(player_id: int) -> bytes | None`.
- Produces: `EditFile.replace_edited_player_entry(player_id: int, entry: bytes) -> None`.
- Produces: `assess_update(edit_file: EditFile, spec: PlayerSpec, all_players: Mapping[int, PlayerInfo]) -> SpecResult`.
- Produces: `apply_update(edit_file: EditFile, spec: PlayerSpec, all_players: Mapping[int, PlayerInfo]) -> SpecResult`.

- [ ] **Step 1: Write three-way update and bit-preservation tests**

Add:

```python
def test_update_applies_only_when_all_current_values_match():
    edit_file = make_player_spec_edit_file_with_palestra()
    spec = marco_spec()
    before = edit_file.get_edited_player_entry(162196)

    result = apply_update(edit_file, spec, edit_file.get_all_players(include_base_db=False))
    after = edit_file.get_edited_player_entry(162196)

    assert result.status == "updated"
    assert decode_player_entry(after).abilities["speed"] == 80
    assert unchanged_bits(before, after, changed_fields=spec.patches) is True


def test_update_is_idempotent_and_third_value_conflicts():
    edit_file = make_player_spec_edit_file_with_palestra()
    spec = marco_spec()
    assert apply_update(edit_file, spec, current_players(edit_file)).status == "updated"
    applied = bytes(edit_file._data)
    assert apply_update(edit_file, spec, current_players(edit_file)).status == "already_applied"
    assert bytes(edit_file._data) == applied

    conflicting = make_player_spec_edit_file_with_palestra(speed=79)
    before = bytes(conflicting._data)
    result = apply_update(conflicting, spec, current_players(conflicting))
    assert result.status == "conflict"
    assert bytes(conflicting._data) == before
```

Also test mixed state (`speed == to`, `acceleration == from`) as `conflict`, not partial application.

- [ ] **Step 2: Run update tests and verify missing methods**

Run: `.venv/bin/python -m pytest tests/test_player_specs.py -k update -q`

Expected: failures for missing `apply_update` and edited-entry accessors.

- [ ] **Step 3: Add exact edited-entry accessors**

Implement accessors that scan only the edited-player block, return/copy exactly `PLAYER_ENTRY_SIZE` bytes, reject wrong sizes, and never touch the linked appearance bytes:

```python
def get_edited_player_entry(self, player_id: int) -> bytes | None:
    for index in range(self.player_count):
        offset = self.player_start + index * PLAYER_TOTAL_SIZE
        if struct.unpack_from("<I", self._data, offset + PE_PLAYER_ID)[0] == player_id:
            return bytes(self._data[offset : offset + PLAYER_ENTRY_SIZE])
    return None


def replace_edited_player_entry(self, player_id: int, entry: bytes) -> None:
    if len(entry) != PLAYER_ENTRY_SIZE:
        raise ValueError(f"player entry must be {PLAYER_ENTRY_SIZE} bytes")
    for index in range(self.player_count):
        offset = self.player_start + index * PLAYER_TOTAL_SIZE
        if struct.unpack_from("<I", self._data, offset + PE_PLAYER_ID)[0] == player_id:
            self._data[offset : offset + PLAYER_ENTRY_SIZE] = entry
            self._player_cache = None
            return
    raise ValueError(f"edited-player record {player_id} was not found")
```

Raise `ValueError` when `player_id` has no edited record. Phase one must not synthesize an editable record for catalog-only players.

- [ ] **Step 4: Implement whole-spec three-way assessment**

Decode the current entry once. Flatten whitelisted patches to codec field names. Return:

- `updated` only after every field equals its `from` value and the patched entry is written;
- `already_applied` only when every field equals its `to` value;
- `conflict` for every mixed or third-value state;
- `rejected` when identity mismatches or no edited record exists.

Patch the copied entry with one `patch_player_entry` call, then replace it atomically. Never loop through fields writing incrementally.

- [ ] **Step 5: Run update and codec tests**

Run: `.venv/bin/python -m pytest tests/test_player_specs.py tests/test_player_codec.py -q`

Expected: update semantics and unknown-bit preservation pass.

- [ ] **Step 6: Commit Task 3**

```bash
git add editor/editfile.py editor/player_spec.py tests/test_player_specs.py tests/test_player_codec.py
git commit -m "feat(players): add guarded stat patches"
```

---

### Task 4: Lifecycle-Aware Batch Engine and Initial Specs

**Files:**
- Create: `players/dastan-satpayev.json`
- Create: `players/marco-palestra.json`
- Modify: `editor/player_spec.py`
- Modify: `tests/test_player_specs.py`
- Create: `tests/test_player_spec_integration.py`

**Interfaces:**
- Consumes: Task 2 `apply_create`, Task 3 `apply_update`, and Task 1 `BaseManifest`/`PlayerSpec`.
- Produces: `apply_player_spec(edit_file, spec, base_revision, all_players) -> SpecResult`.
- Produces: `apply_player_specs(edit_file, specs, base_revision, all_players) -> tuple[SpecResult, ...]`.

- [ ] **Step 1: Add lifecycle and independent-batch tests**

```python
def test_new_base_revision_skips_old_spec_before_mutation():
    edit_file = make_player_spec_edit_file_with_palestra()
    before = bytes(edit_file._data)
    result = apply_player_spec(edit_file, marco_spec(), "fl26-u2.3", current_players(edit_file))
    assert (result.status, result.reason) == ("needs_review", "base_revision_not_reviewed")
    assert bytes(edit_file._data) == before


def test_waiting_create_does_not_block_valid_update():
    edit_file = make_combined_fixture(chelsea_roster_size=40)
    results = apply_player_specs(
        edit_file,
        (dastan_spec(), marco_spec()),
        "fl26-u2.2-national-squads",
        current_players(edit_file),
    )
    assert [result.status for result in results] == ["waiting", "updated"]
```

Create `tests/test_player_spec_integration.py` with a real encrypted-base contract:

```python
def test_bundled_base_batch_survives_encryption_roundtrip(tmp_path):
    source = tmp_path / "EDIT00000000"
    shutil.copy2("base/EDIT00000000", source)
    decrypted = crypto.decrypt(source)
    reopened = None
    try:
        edit_file = EditFile()
        edit_file.load(decrypted)
        results = apply_player_specs(
            edit_file,
            load_player_specs(),
            load_base_manifest().revision,
            edit_file.get_all_players(),
        )
        assert {result.name: result.status for result in results} == {
            "Dastan Satpayev": "waiting",
            "Marco Palestra": "updated",
        }
        assert edit_file.validate_integrity()["valid"] is True
        edit_file.save(decrypted)

        output = tmp_path / "updated-EDIT00000000"
        crypto.encrypt(decrypted, output)
        reopened = crypto.decrypt(output)
        verified = EditFile()
        verified.load(reopened)
        assert verified.validate_integrity()["valid"] is True
        assert verified.get_player_ability_profile(162196).abilities["speed"] == 80
        assert verified.get_all_players().get(200000) is None
    finally:
        crypto.cleanup_temp(decrypted)
        if reopened is not None:
            crypto.cleanup_temp(reopened)
```

Add the available-slot contract to the same file:

```python
def test_bundled_base_create_survives_encryption_roundtrip(tmp_path):
    source = tmp_path / "EDIT00000000"
    shutil.copy2("base/EDIT00000000", source)
    decrypted = crypto.decrypt(source)
    reopened = None
    try:
        edit_file = EditFile()
        edit_file.load(decrypted)
        assert edit_file.release_player(126925, 102) is True
        dastan = next(spec for spec in load_player_specs() if spec.identity.pes_id == 200000)
        result = apply_player_spec(
            edit_file,
            dastan,
            load_base_manifest().revision,
            edit_file.get_all_players(),
        )
        assert result.status == "created"
        assert len(edit_file.get_team_roster(102).roster) == 40
        assert edit_file.validate_integrity()["valid"] is True
        edit_file.save(decrypted)

        output = tmp_path / "created-EDIT00000000"
        crypto.encrypt(decrypted, output)
        reopened = crypto.decrypt(output)
        verified = EditFile()
        verified.load(reopened)
        assert verified.validate_integrity()["valid"] is True
        assert verified.get_team_roster(102).player_index(200000) != -1
        assert verified.get_all_players()[200000].name == "Dastan Satpayev"
    finally:
        crypto.cleanup_temp(decrypted)
        if reopened is not None:
            crypto.cleanup_temp(reopened)
```

Test `upstreamed` and `retired` specs as report-only, deterministic filename ordering, one failed mutation restoring only that player's bytes, and final no-op when no spec changes.

- [ ] **Step 2: Run lifecycle tests and verify failures**

Run: `.venv/bin/python -m pytest tests/test_player_specs.py -k "revision or batch or lifecycle" -q`

Expected: missing dispatcher/batch failures.

- [ ] **Step 3: Check in the complete Dastan create spec**

Migrate every value from the current `data/curated_players.json` into the schema from Task 1. Use:

```json
"lifecycle": {"status": "active", "reason": "Missing from bundled FL26 base"},
"applies_to": ["fl26-u2.2-national-squads"]
```

Retain PES ID `200000`, SortitoutSI ID `2000370206`, Chelsea team ID `102`, preferred shirt `36`, all 25 abilities, positions, skills, generic appearance inputs, effective date `2026-08-04`, and all four existing evidence URLs.

- [ ] **Step 4: Check in the Marco update spec**

Use PES ID `162196`, SortitoutSI ID `2000136198`, lifecycle `active`, base revision `fl26-u2.2-national-squads`, and these exact patches:

```json
"speed": {"from": 77, "to": 80},
"acceleration": {"from": 75, "to": 77},
"defensive_awareness": {"from": 61, "to": 62},
"ball_winning": {"from": 59, "to": 60}
```

Evidence includes the SortitoutSI person page and approved submission `526121`. Do not add an OVR field.

- [ ] **Step 5: Implement lifecycle dispatch and deterministic batches**

Check lifecycle and base compatibility before loading player records from the save. Return computed statuses for inactive/incompatible specs. Dispatch active compatible specs by `operation`. Continue after `waiting`, `conflict`, `needs_review`, `upstreamed`, and `retired`. Treat `rejected` and unexpected exceptions as per-spec rollback results while preserving prior successful independent changes for final validation.

- [ ] **Step 6: Run all player-spec tests**

Run: `.venv/bin/python -m pytest tests/test_player_specs.py tests/test_player_codec.py tests/test_player_spec_integration.py -q`

Expected: both checked-in specs load; Dastan waits on the real full Chelsea roster, Marco updates from the exact baseline, and both save scenarios survive Linux encryption round trips.

- [ ] **Step 7: Commit Task 4**

```bash
git add players/dastan-satpayev.json players/marco-palestra.json editor/player_spec.py tests/test_player_specs.py tests/test_player_spec_integration.py
git commit -m "feat(players): add initial reviewed specs"
```

---

### Task 5: Explicit `players validate` and `players apply` CLI

**Files:**
- Modify: `run.py`
- Modify: `editor/logger.py`
- Modify: `tests/test_run_pipeline.py`
- Modify: `tests/test_logger.py`

**Interfaces:**
- Consumes: `load_base_manifest`, `load_player_specs`, `validate_spec_set`, and `apply_player_specs`.
- Produces: `cmd_players_validate(args) -> None` and `cmd_players_apply(args) -> None`.
- Produces CLI: `python run.py players validate` and `python run.py players apply --base-revision REVISION --edit-file PATH (--in-place | --output PATH)`.

- [ ] **Step 1: Write failing explicit-command tests**

Add parser and behavior tests:

```python
def test_players_validate_rejects_wrong_pristine_base_digest(monkeypatch, tmp_path):
    import run
    monkeypatch.setattr(run.config, "BASE_EDIT_PATH", tmp_path / "EDIT00000000")
    (tmp_path / "EDIT00000000").write_bytes(b"wrong")
    with pytest.raises(SystemExit) as exc:
        run.cmd_players_validate(Namespace())
    assert exc.value.code == 2


def test_players_apply_writes_only_after_successful_roundtrip(monkeypatch, tmp_path):
    import run
    from editor.player_spec import SpecResult

    calls = []
    source = tmp_path / "EDIT00000000"
    output = tmp_path / "updated"
    source.write_bytes(b"encrypted")
    decrypted = tmp_path / "data.dat"
    decrypted.write_bytes(b"decrypted")

    class FakeEditFile:
        def load(self, path):
            calls.append("load")

        def get_all_players(self, include_base_db=True):
            return {}

        def validate_integrity(self):
            calls.append("validate")
            return {"valid": True, "errors": [], "warnings": [], "metrics": {}}

        def save(self, path):
            calls.append("save")
            Path(path).write_bytes(b"changed")

    decrypt_count = 0

    def fake_decrypt(path):
        nonlocal decrypt_count
        decrypt_count += 1
        calls.append("decrypt-input" if decrypt_count == 1 else "decrypt-verify")
        return decrypted

    monkeypatch.setattr(run.crypto, "decrypt", fake_decrypt)
    monkeypatch.setattr(run.crypto, "encrypt", lambda source_path, output_path: calls.append("encrypt"))
    monkeypatch.setattr(run, "EditFile", FakeEditFile)
    monkeypatch.setattr(run, "load_player_specs", lambda: (marco_spec(),))
    monkeypatch.setattr(
        run,
        "apply_player_specs",
        lambda *args: (SpecResult(162196, "Marco Palestra", "updated", "baseline_matched"),),
    )
    monkeypatch.setattr(
        run.transfer_logger,
        "log_transfer",
        lambda **record: calls.append(("audit", record["transfer_type"])),
    )

    run.cmd_players_apply(
        Namespace(
            edit_file=str(source),
            output=str(output),
            in_place=False,
            base_revision="fl26-u2.2-national-squads",
        )
    )

    assert calls.index("save") < calls.index("encrypt")
    assert calls.index("encrypt") < calls.index("decrypt-verify")
    assert calls.index("decrypt-verify") < calls.index(("audit", "player_spec_update"))
```

Keep the fake aligned with the command interface as it is implemented; the invariant is that audit persistence occurs only after output encryption, decrypt verification, and integrity validation. Assert a `player_spec_update` audit record for Marco.

Test a no-change batch does not create a backup or write output. Test `needs_review` appears in the report but does not mutate.

- [ ] **Step 2: Run command tests and confirm parser/handler failures**

Run: `.venv/bin/python -m pytest tests/test_run_pipeline.py tests/test_logger.py -k players -q`

Expected: failures for missing nested `players` subcommands.

- [ ] **Step 3: Implement `players validate`**

Load `base_manifest.json`, compute SHA-256 of `base/EDIT00000000`, fail with exit code `2` on mismatch, load all specs, validate the global set, decrypt the base, and run semantic assessment without mutation. Print counts for active, needs-review, upstreamed, retired, create, and update specs.

- [ ] **Step 4: Implement `players apply` as an explicit save transaction**

Reuse the existing lock, backup, decrypt, integrity, concurrent-input digest, save, encrypt, and round-trip verification patterns from `cmd_run`. Do not call transfer scraping or matching. Require `--base-revision` to equal `base_manifest.revision`; otherwise exit `2` before decrypting. Save only when at least one result is `created` or `updated`.

Persist audit/report records after successful encryption verification. Use distinct `transfer_type` values `player_spec_create` and `player_spec_update`; report sections must label them as player creations and player updates, not club transfers.

- [ ] **Step 5: Extend logger report semantics**

Generalize the current curated creation branch to recognize `player_spec_create` and add a separate update section for `player_spec_update`. Metrics must not count either as a club transfer or permanent move. Update records show field names and `from -> to` values.

- [ ] **Step 6: Run CLI and logger tests**

Run: `.venv/bin/python -m pytest tests/test_run_pipeline.py tests/test_logger.py tests/test_player_specs.py -q`

Expected: explicit commands, audit ordering, no-op behavior, and report classification pass.

- [ ] **Step 7: Commit Task 5**

```bash
git add run.py editor/logger.py tests/test_run_pipeline.py tests/test_logger.py
git commit -m "feat(players): add explicit apply command"
```

---

### Task 6: Remove Implicit Pilot and Wire Sync Workflows Explicitly

**Files:**
- Remove: `data/curated_players.json`
- Remove: `editor/curated_player.py`
- Modify: `config.py`
- Modify: `run.py`
- Modify: `.github/workflows/sync-fast.yml`
- Modify: `.github/workflows/sync-deep.yml`
- Modify: `tests/test_run_pipeline.py`
- Modify: `README.md`
- Modify: `MEMORY.md`

**Interfaces:**
- Consumes: Task 5 explicit `players apply` command.
- Produces: transfer-only `cmd_run`; sync workflows explicitly call both transfer sync and player-spec apply.

- [ ] **Step 1: Write a regression test proving `run` ignores player specs**

```python
def test_transfer_run_never_loads_or_applies_player_specs(monkeypatch, tmp_path):
    import run
    monkeypatch.setattr(
        run,
        "load_player_specs",
        lambda *_: (_ for _ in ()).throw(AssertionError("implicit player specs")),
        raising=False,
    )
    # Use the existing no-transfer command fixture.
    run.cmd_run(no_transfer_args(tmp_path))
    assert "No transfers found" in captured_output()
```

Also assert `run.py run --help` contains no player-spec application option.

- [ ] **Step 2: Run the regression test before cleanup**

Run: `.venv/bin/python -m pytest tests/test_run_pipeline.py::test_transfer_run_never_loads_or_applies_player_specs -q`

Expected: fail because current `cmd_run` still loads and applies curated players.

- [ ] **Step 3: Remove current curated integration completely**

Delete imports and functions `_plan_curated_players`, `_curated_audit_record`, curated dry-run output, automatic load/apply loops, counters, and current pilot-only tests. Remove `CURATED_PLAYERS_FILE` and delete the monolithic manifest/module. Preserve the generic player codec, explicit player-spec command, and generic logger report support.

Restore no-transfer behavior in `cmd_run`: print the transfer-only no-op message and return without decrypting solely for player specs.

- [ ] **Step 4: Make sync workflows explicit**

For both fast and deep workflows:

1. verify the pristine base and `data/base_manifest.json`;
2. copy `base/EDIT00000000` to `output/EDIT00000000`;
3. run transfer sync against the output in place;
4. run:

```bash
python run.py players apply \
  --base-revision fl26-u2.2-national-squads \
  --edit-file output/EDIT00000000 \
  --in-place
```

Keep artifact paths for the output save, JSONL audit, Markdown report, and HTML report.

- [ ] **Step 5: Update project documentation**

README documents one-file-per-player contributions, explicit `players validate/apply`, lifecycle on official base updates, and the fact that `run` handles transfers only. MEMORY documents `player_spec.py`, `players/`, `base_manifest.json`, and removes monolithic curated-pilot descriptions.

- [ ] **Step 6: Run transfer-only and workflow-adjacent tests**

Run: `.venv/bin/python -m pytest tests/test_run_pipeline.py tests/test_config.py tests/test_logger.py -q`

Expected: transfer behavior is unchanged, no implicit spec access occurs, and explicit player commands remain green.

- [ ] **Step 7: Commit Task 6**

```bash
git add config.py run.py .github/workflows/sync-fast.yml .github/workflows/sync-deep.yml tests/test_run_pipeline.py README.md MEMORY.md
git rm data/curated_players.json editor/curated_player.py
git commit -m "refactor(players): remove implicit pilot"
```

---

### Task 7: Real-Save Verification and Final Cleanup

**Files:**
- Modify only if verification exposes a real defect: files from Tasks 1-6.

**Interfaces:**
- Verifies all prior interfaces end to end.

- [ ] **Step 1: Run the complete test suite**

Run: `.venv/bin/python -m pytest -q`

Expected: all tests pass on the repository's Python environment.

- [ ] **Step 2: Validate specs and pristine base**

Run: `.venv/bin/python run.py players validate`

Expected: base digest matches, both specs load, Dastan is a valid create candidate, and Marco's four baseline values match.

- [ ] **Step 3: Smoke test current full-roster behavior**

Run `players apply` against a temporary copy of the current bundled base. Expected results:

- Dastan: `waiting`, destination roster full, no player or appearance record appended;
- Marco: `updated`, exactly four approved fields changed;
- output passes integrity and encrypt/decrypt verification;
- original `base/EDIT00000000` digest remains `498b3bdf70084991a05a6d765405ff17e3f89af87869dceccb29fb2a06a8f10c`.

- [ ] **Step 4: Smoke test Dastan creation with a temporary slot**

On a temporary decrypted copy only, release the final Chelsea roster slot, run the explicit player-spec engine, encrypt, decrypt, and assert:

- Dastan PES ID `200000` exists with the reviewed abilities;
- Dastan is registered to Chelsea;
- roster size returns to `40`;
- the released temporary player is absent;
- final integrity is valid.

- [ ] **Step 5: Smoke test official-base lifecycle**

Invoke assessment with base revision `fl26-u2.3` without altering the save. Both active specs must return `needs_review`; bytes remain identical.

- [ ] **Step 6: Inspect CLI surface**

Run: `.venv/bin/python run.py --help && .venv/bin/python run.py players --help`

Expected: transfer `run` and explicit `players validate/apply` are separate; no implicit player-spec flag appears under `run`.

- [ ] **Step 7: Commit verification-only fixes if any**

If and only if Steps 1-6 expose a real defect, add its regression test and commit the focused fix with a concrete subject such as `fix(players): preserve update bits`. Do not create an empty cleanup commit.
