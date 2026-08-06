# Pes Retro Stats Mature Player Proposals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn one canonical Pes Retro Stats profile into a complete, reproducible update or create proposal with automatic PES internal-field resolution, OVR review, trusted CI validation, and explicit human approval.

**Architecture:** Keep fetching and source normalization in `scraper`, place deterministic OVR calculation in `editor`, and add focused proposal snapshot/resolution/review modules under `tools`. Generation and trusted validation share the same pure builders; CI recomputes from an embedded normalized snapshot and the verified base without network access. A complete proposal passes validation but remains blocked from apply until `players approve` atomically removes draft-only metadata.

**Tech Stack:** Python 3.13+, standard library, `aiohttp`, immutable dataclasses/mapping proxies, pytest, GitHub Actions YAML, existing PES 2021 codec and EditFile APIs.

## Global Constraints

- Work directly on branch `main`; do not create a worktree unless the user explicitly requests one.
- Use TDD for every behavioral change: write one observable failing test, run it red, implement minimally, then run it green.
- Add no runtime dependency.
- Target base revision remains exactly `fl26-u2.2-national-squads`.
- A canonical Retro profile must produce a complete update or create proposal with no `null`, `missing`, or unresolved placeholder.
- Contributors and maintainers never enter raw PES player, team, or nationality IDs.
- Source model is exactly `pes-retro-normalized-v1`; generator model is exactly `pes-retro-mature-proposal-v1`; OVR model is exactly `pes2021-community-estimate-v1`.
- CI must not refetch Pes Retro Stats; it recomputes from the embedded normalized snapshot and verified base.
- `snapshot_sha256` proves internal integrity, not web authenticity; proposal metadata is accepted only from the matching same-repository `player-draft/issue-<N>` branch and issue evidence.
- OVR is derived review metadata only. It never chooses abilities, mutates a save field, releases a player, or represents an official in-game rating.
- OVR uses integer hundredth weights and exact half-up tenths: `(weighted_sum * 20 + total_weight) // (2 * total_weight)`.
- Created-player IDs occupy `200000..299999` and use the UUID-seeded circular allocation specified in the design.
- Create appearance uses exact `appearance-palette-v1`: `(3,17), (2,17), (4,17), (1,17), (5,17), (12,16), (30,17), (9,17)`.
- Complete unapproved proposals validate successfully but `players apply` must reject them without a bypass flag.
- Approval remains an explicit human action and does not commit, push, merge, or apply a player.
- Completed specs contain neither the normalized `source` snapshot nor `draft`/OVR metadata.
- Preserve the pull-request security boundary: one canonical `players/<slug>.json`, trusted base code only, no head-code execution, no new permissions, no secret access.
- Follow clean cutover: migrate all callsites and tests; leave no deprecated aliases or alternate schema path.
- Every task stages only its named files because unrelated user changes may be present on `main`.

---

## File Structure

**Create:**

- `editor/player_ovr.py` — strict deterministic OVR weights, calculation, and relevant-position selection.
- `scraper/pes_retro_snapshot.py` — normalized profile snapshot serialization, canonical hashing, and strict reconstruction.
- `tools/player_proposal_resolution.py` — nationality catalog plus team, player-ID, print-name, and generic-appearance resolution.
- `tools/player_proposal_review.py` — operation-specific OVR review construction and exact schema validation.
- `data/pes21_nationalities.json` — offline PES 2021 nationality mapping with provenance and aliases.
- `tests/test_player_ovr.py` — shared OVR contracts.
- `tests/test_pes_retro_snapshot.py` — snapshot roundtrip/tamper contracts.
- `tests/test_player_proposal_resolution.py` — internal-field resolver contracts.
- `tests/test_player_proposal_review.py` — OVR review schema and recomputation contracts.
- `tests/test_ovr_calc.py` — CLI adapter behavior.
- `tests/test_readme_roadmap.py` — translated roadmap completion contracts.
- `tools/check_player_proposal_origin.py` — trusted proposal-origin policy and CLI.
- `tests/test_player_proposal_origin.py` — fork/branch/issue provenance contracts.

**Modify:**

- `tools/ovr_calc.py` — remove formula copy and consume `editor.player_ovr`.
- `tools/generate_player_draft.py` — emit complete update/create proposals and validate them offline.
- `editor/player_spec.py` — parse complete proposal metadata separately from approved specs.
- `run.py` — proposal-aware validation, apply gate, and `players approve` command.
- `tests/test_generate_player_draft.py` — mature generation behavior.
- `tests/test_player_specs.py` — complete proposal schema and apply gate.
- `tests/test_run_pipeline.py` — CLI validation/approval behavior.
- `tests/test_player_spec_integration.py` — proposal → approval → apply roundtrips.
- `.github/workflows/generate-player-update.yml` — complete-proposal PR wording.
- `.github/workflows/validate-player-update-pr.yml` — preserve trusted validation while accepting a complete proposal.
- `tests/test_workflow_config.py` — workflow security and copy assertions.
- `README.md`, `README.id.md`, `README.es.md`, `README.pt.md`, `README.ar.md`, `README.zh.md`, `README.it.md`, `README.ru.md`, `README.de.md`, `README.fr.md`, `README.tr.md` — remove the completed Pes Retro Stats/OVR roadmap item while preserving the Local Update item.

---

### Task 1: Shared Deterministic OVR Engine

**Files:**
- Create: `editor/player_ovr.py`
- Create: `tests/test_player_ovr.py`
- Create: `tests/test_ovr_calc.py`
- Modify: `tools/ovr_calc.py`

**Interfaces:**
- Consumes: `editor.player_codec.ABILITY_FIELDS` and `POSITION_NAMES`.
- Produces: `OVR_MODEL`, `PlayerOvrError`, `calculate_ovr_tenths(abilities, position)`, and `relevant_ovr_positions(registered_position, position_proficiency)`.

- [ ] **Step 1: Write failing all-position and validation tests**

Create `tests/test_player_ovr.py` with an independently obvious constant vector and this asymmetric vector:

```python
import pytest

from editor.player_codec import ABILITY_FIELDS, POSITION_NAMES
from editor.player_ovr import (
    PlayerOvrError,
    calculate_ovr_tenths,
    relevant_ovr_positions,
)


def test_equal_abilities_produce_the_same_ovr_for_every_position():
    abilities = {field: 60 for field in ABILITY_FIELDS}
    assert {
        position: calculate_ovr_tenths(abilities, position)
        for position in POSITION_NAMES
    } == {position: 600 for position in POSITION_NAMES}


def test_asymmetric_vector_has_exact_position_weighted_results():
    abilities = {
        field: 40 + index * 2 for index, field in enumerate(ABILITY_FIELDS)
    }
    assert {
        position: calculate_ovr_tenths(abilities, position)
        for position in POSITION_NAMES
    } == {
        "GK": 720,
        "CB": 623,
        "LB": 604,
        "RB": 604,
        "DMF": 614,
        "CMF": 578,
        "LMF": 575,
        "RMF": 575,
        "AMF": 549,
        "LWF": 553,
        "RWF": 553,
        "SS": 548,
        "CF": 551,
    }


def test_relevant_positions_are_unique_and_codec_ordered():
    assert relevant_ovr_positions(
        "RB", {"CF": 2, "RB": 2, "LWF": 1, "GK": 0}
    ) == ("RB", "LWF", "CF")


@pytest.mark.parametrize(
    "abilities, position",
    [
        ({field: 60 for field in ABILITY_FIELDS[:-1]}, "CF"),
        ({**{field: 60 for field in ABILITY_FIELDS}, "extra": 60}, "CF"),
        ({**{field: 60 for field in ABILITY_FIELDS}, "speed": True}, "CF"),
        ({**{field: 60 for field in ABILITY_FIELDS}, "speed": 100}, "CF"),
        ({field: 60 for field in ABILITY_FIELDS}, "RWB"),
    ],
)
def test_ovr_fails_closed_for_invalid_inputs(abilities, position):
    with pytest.raises(PlayerOvrError):
        calculate_ovr_tenths(abilities, position)
```

Add a focused half-up test using a deliberately tiny injected/private weight fixture only if the production table has no exact `.05` midpoint; never assert via `round()`.

- [ ] **Step 2: Run the OVR tests and verify RED**

Run: `python -m pytest tests/test_player_ovr.py -q`

Expected: collection error because `editor.player_ovr` does not exist.

- [ ] **Step 3: Implement the strict integer OVR module**

Create `editor/player_ovr.py` with the prototype table converted to integer hundredths and this calculation shape:

```python
OVR_MODEL = "pes2021-community-estimate-v1"


class PlayerOvrError(ValueError):
    pass


def calculate_ovr_tenths(abilities, position):
    if set(abilities) != set(ABILITY_FIELDS):
        raise PlayerOvrError("OVR abilities must exactly match ABILITY_FIELDS")
    for field in ABILITY_FIELDS:
        value = abilities[field]
        if type(value) is not int or not 40 <= value <= 99:
            raise PlayerOvrError(f"OVR ability {field} must be an integer from 40 to 99")
    normalized = position.upper() if isinstance(position, str) else ""
    if normalized not in POSITION_NAMES:
        raise PlayerOvrError(f"unsupported OVR position: {position!r}")
    index = POSITION_NAMES.index(normalized)
    total_weight = sum(_WEIGHTS[field][index] for field in ABILITY_FIELDS)
    if total_weight <= 0:
        raise PlayerOvrError(f"OVR position {normalized} has no weights")
    weighted_sum = sum(
        _WEIGHTS[field][index] * abilities[field] for field in ABILITY_FIELDS
    )
    return (weighted_sum * 20 + total_weight) // (2 * total_weight)
```

Implement relevant-position validation with exact codec ordering, integer proficiency grades `0..2`, and registered-position inclusion.

- [ ] **Step 4: Run OVR tests and verify GREEN**

Run: `python -m pytest tests/test_player_ovr.py -q`

Expected: all tests pass.

- [ ] **Step 5: Write failing CLI adapter tests**

Create `tests/test_ovr_calc.py` to monkeypatch only base loading and assert `_print_ovr_table` renders `60.0`, `calc_ovr` no longer exists in `tools.ovr_calc`, and a partial spec ability map raises a concise error instead of defaulting absent fields to `40`.

- [ ] **Step 6: Run CLI tests and verify RED**

Run: `python -m pytest tests/test_ovr_calc.py -q`

Expected: failure because the CLI still owns `calc_ovr` and accepts partial maps.

- [ ] **Step 7: Migrate the CLI to the shared engine**

Import `calculate_ovr_tenths` and render with:

```python
def _format_ovr(value_tenths: int) -> str:
    return f"{value_tenths // 10}.{value_tenths % 10}"
```

Remove `_WEIGHTS`, `_POS_IDX`, `calc_ovr`, and `abilities.get(field, 40)`. Validate spec-derived abilities as a complete post-patch map before calculation.

- [ ] **Step 8: Run focused tests and commit**

Run: `python -m pytest tests/test_player_ovr.py tests/test_ovr_calc.py -q`

Expected: all tests pass.

```bash
git add editor/player_ovr.py tools/ovr_calc.py tests/test_player_ovr.py tests/test_ovr_calc.py
git commit -m "feat(players): add deterministic OVR engine"
```

---

### Task 2: Offline Normalized Retro Snapshot

**Files:**
- Create: `scraper/pes_retro_snapshot.py`
- Create: `tests/test_pes_retro_snapshot.py`
- Modify: `scraper/__init__.py`

**Interfaces:**
- Consumes: `scraper.pes_retro_stats.PesRetroStatsProfile`.
- Produces: `SOURCE_MODEL`, `PesRetroSnapshotError`, `profile_to_snapshot(profile) -> dict[str, object]`, and `profile_from_snapshot(value) -> PesRetroStatsProfile`.

- [ ] **Step 1: Write failing roundtrip, canonical-hash, and tamper tests**

Use the existing complete profile fixture pattern from `tests/test_pes_retro_stats.py`. Assert:

```python
snapshot = profile_to_snapshot(profile)
assert set(snapshot) == {"model", "data", "snapshot_sha256"}
assert snapshot["model"] == "pes-retro-normalized-v1"
canonical = json.dumps(
    {"model": snapshot["model"], "data": snapshot["data"]},
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
).encode("utf-8")
assert snapshot["snapshot_sha256"] == hashlib.sha256(canonical).hexdigest()
assert profile_from_snapshot(snapshot) == profile
```

Parameterize one-field tampering, unknown keys, bool-as-int, missing complete stat/position keys, duplicate skills/styles, noncanonical URL/UUID, and an invalid hash. Assert all raise `PesRetroSnapshotError` without including a raw payload dump.

- [ ] **Step 2: Run snapshot tests and verify RED**

Run: `python -m pytest tests/test_pes_retro_snapshot.py -q`

Expected: collection error because `scraper.pes_retro_snapshot` does not exist.

- [ ] **Step 3: Implement canonical serialization and strict reconstruction**

Use a JSON-safe `data` object whose keys mirror every `PesRetroStatsProfile` field. Serialize dates as ISO `YYYY-MM-DD`, mappings as plain dictionaries in canonical source-key order, and tuples as lists. Reconstruct immutable tuples and `MappingProxyType` mappings. Reject unknown or incomplete keys before constructing the dataclass.

Canonical hashing must use exactly:

```python
def _canonical_bytes(model: str, data: Mapping[str, object]) -> bytes:
    return json.dumps(
        {"model": model, "data": data},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
```

Validate through the existing canonical URL parser and require the UUID prefix to match the profile URL short ID.

- [ ] **Step 4: Export the snapshot API and run GREEN**

Update `scraper/__init__.py` to export the four new names. Run:

`python -m pytest tests/test_pes_retro_snapshot.py tests/test_pes_retro_stats.py tests/test_pes21_proposal.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add scraper/pes_retro_snapshot.py scraper/__init__.py tests/test_pes_retro_snapshot.py
git commit -m "feat(players): add offline Retro snapshots"
```

---

### Task 3: Deterministic Create-Field Resolution

**Files:**
- Create: `tools/player_proposal_resolution.py`
- Create: `data/pes21_nationalities.json`
- Create: `tests/test_player_proposal_resolution.py`

**Interfaces:**
- Consumes: verified `EditFile`, `PesRetroStatsProfile`, completed spec IDs, `normalize_player_identity`, team alias data.
- Produces: `NationalityCatalog`, `CreateResolution`, `load_nationality_catalog`, `resolve_create_team`, `allocate_created_player_id`, `derive_print_name`, `derive_generic_appearance`, and `resolve_create_fields`.

- [ ] **Step 1: Add failing nationality-catalog tests**

Define the committed data shape:

```json
{
  "schema_version": 1,
  "source": {
    "url": "https://github.com/xAranaktu/PES-2021-Cheat-Table/blob/master/PES%202021%20-%20v21.1.0.CT",
    "license": "MIT",
    "copyright": "Copyright (c) 2020 Paweł"
  },
  "nationalities": [
    {"id": 1, "name": "Afghanistan", "aliases": []}
  ]
}
```

Tests must require 220 nonzero records from the upstream PES 2021 table and at least these exact resolutions:

```python
assert catalog.resolve("Indonesia") == 10
assert catalog.resolve("Kazakhstan") == 216
assert catalog.resolve("Italy") == 215
assert catalog.resolve("United Arab Emirates") == 37
assert catalog.resolve("DR Congo") == 55
assert catalog.resolve("Ivory Coast") == 56
assert catalog.resolve("North Macedonia") == 221
assert catalog.resolve("United States of America") == 135
```

Reject duplicate IDs, duplicate normalized canonical names/aliases, ID `0`, ID above `65535`, empty text, bool IDs, unknown nationality, and ambiguous alias input.

- [ ] **Step 2: Run catalog tests and verify RED**

Run: `python -m pytest tests/test_player_proposal_resolution.py -q`

Expected: collection error because the resolver module and data file do not exist.

- [ ] **Step 3: Commit the complete offline nationality mapping**

Transcribe all 220 nonzero entries from the MIT-licensed PES 2021 `Nationality` dropdown at:

- Mapping source: `https://raw.githubusercontent.com/xAranaktu/PES-2021-Cheat-Table/master/PES%202021%20-%20v21.1.0.CT`
- License: `https://raw.githubusercontent.com/xAranaktu/PES-2021-Cheat-Table/master/LICENSE`

Preserve source numeric IDs and canonical labels. Add only explicit normalized aliases required by Retro naming, including the eight assertions above plus `Comoros`, `Gambia`, `Eswatini`, `Congo Republic`, `Guyana`, and `São Tomé and Príncipe`. Tests are offline and must never download this source.

- [ ] **Step 4: Implement strict catalog loading**

Use immutable ID/name maps and fail on every schema or normalization collision. Default `load_nationality_catalog()` to `config.DATA_DIR / "pes21_nationalities.json"`, with an injectable path for tests.

- [ ] **Step 5: Add failing deterministic resolver tests**

Cover:

```python
assert allocate_created_player_id(UUID_TEXT, set()) == EXPECTED_HASHED_ID
assert allocate_created_player_id(UUID_TEXT, {EXPECTED_HASHED_ID}) == (
    200000 + ((EXPECTED_HASHED_ID - 200000 + 1) % 100000)
)
assert derive_generic_appearance(UUID_TEXT) == EXPECTED_PALETTE_PAIR
assert derive_print_name("Dastan Satpaev") == "SATPAEV"
assert derive_print_name("Pelé") == "PELÉ"
```

Derive `EXPECTED_HASHED_ID` independently in the test with `hashlib.sha256(UUID_TEXT.encode()).digest()[:8]`, not by calling a production helper. Cover circular wrap, full-range exhaustion via an injectable small range, ID collisions from base plus specs, Unicode punctuation/whitespace, over-60-byte rejection, exact team/alias matching, conflicting submitted/source teams, ambiguous teams, and rosterless teams.

- [ ] **Step 6: Implement create resolution**

Define:

```python
@dataclass(frozen=True, slots=True)
class CreateResolution:
    player_id: int
    print_name: str
    team_id: int
    team_name: str
    nationality_id: int
    skin_color: int
    iris_color: int
```

`resolve_create_fields` must resolve both team names to the same `TeamInfo`, resolve source nationality, derive ID/print name/appearance, and return a fully populated immutable object. It must not mutate `EditFile`.

- [ ] **Step 7: Run focused tests and commit**

Run: `python -m pytest tests/test_player_proposal_resolution.py -q`

Expected: all tests pass.

```bash
git add tools/player_proposal_resolution.py data/pes21_nationalities.json tests/test_player_proposal_resolution.py
git commit -m "feat(players): resolve create fields automatically"
```

---

### Task 4: Complete Proposal Builder and OVR Review

**Files:**
- Create: `tools/player_proposal_review.py`
- Create: `tests/test_player_proposal_review.py`
- Modify: `tools/generate_player_draft.py`
- Modify: `tests/test_generate_player_draft.py`

**Interfaces:**
- Consumes: Tasks 1–3 APIs, `PlayerDraftRequest`, verified `EditFile`, completed-spec IDs.
- Produces: `build_ovr_review`, `validate_ovr_review_shape`, complete `build_player_draft`, and complete `write_player_draft` output.

- [ ] **Step 1: Write failing OVR review tests**

Test exact update and create shapes:

```python
assert build_ovr_review(
    operation="update",
    base_abilities=base,
    proposal_abilities=proposal,
    registered_position="RB",
    position_proficiency={"RB": 2, "RWF": 1},
) == {
    "model": "pes2021-community-estimate-v1",
    "mode": "comparison",
    "positions": [
        {
            "position": "RB",
            "base_tenths": calculate_ovr_tenths(base, "RB"),
            "proposal_tenths": calculate_ovr_tenths(proposal, "RB"),
            "delta_tenths": (
                calculate_ovr_tenths(proposal, "RB")
                - calculate_ovr_tenths(base, "RB")
            ),
        },
        {
            "position": "RWF",
            "base_tenths": calculate_ovr_tenths(base, "RWF"),
            "proposal_tenths": calculate_ovr_tenths(proposal, "RWF"),
            "delta_tenths": (
                calculate_ovr_tenths(proposal, "RWF")
                - calculate_ovr_tenths(base, "RWF")
            ),
        },
    ],
}
```

Create mode must omit base/delta. Strict-shape tests reject wrong model/mode, bool values, out-of-range values, incorrect delta, duplicate/out-of-order/irrelevant positions, unknown keys, and operation/mode mismatch.

- [ ] **Step 2: Run review tests and verify RED**

Run: `python -m pytest tests/test_player_proposal_review.py -q`

Expected: collection error because the review module does not exist.

- [ ] **Step 3: Implement the pure OVR review builder**

Use Task 1 APIs only. Return plain JSON objects and validate exact operation-specific row keys. Do not access files, network, or `EditFile`.

- [ ] **Step 4: Replace partial-create expectations with failing mature-proposal tests**

In `tests/test_generate_player_draft.py`:

- make create generation receive the verified fake `EditFile` and nationality catalog;
- assert identity and PES player IDs match and are non-null;
- assert exact resolved Chelsea team ID/name, Kazakhstan ID `216`, generated print name, and deterministic palette pair;
- assert all mapped abilities/positions/skills/styles are present;
- assert `source` is an exact normalized snapshot;
- assert `draft` has exactly generator, `needs_human_review`, and OVR review;
- assert recursive traversal finds no `None` value and no key named `missing`;
- keep update tests but add exact source snapshot and base/proposal OVR comparison;
- assert a no-change update raises `PlayerDraftError` and writes no file.

- [ ] **Step 5: Run generator tests and verify RED**

Run: `python -m pytest tests/test_generate_player_draft.py -q`

Expected: create assertions fail because internal fields are still null and draft contains `missing`.

- [ ] **Step 6: Implement complete update/create proposal generation**

Change `build_player_draft` so `edit_file` is mandatory for both operations. For update, retain exact base resolution/diff behavior, derive the full proposed ability/position state, and add comparison OVR. For create, call `resolve_create_fields`, fill every identity/PES field, and add new-player OVR. Use `profile_to_snapshot(source)` for `source`.

Use exact draft shape:

```python
"draft": {
    "generator": "pes-retro-mature-proposal-v1",
    "needs_human_review": True,
    "ovr_review": ovr_review,
}
```

Always verify/decrypt/load the base in `write_player_draft`, including create operations. Gather occupied IDs from the verified base and completed specs before allocation. Preserve exclusive atomic write and two-line machine output.

- [ ] **Step 7: Run focused generation tests and commit**

Run:

`python -m pytest tests/test_player_proposal_review.py tests/test_generate_player_draft.py tests/test_player_draft_diff.py -q`

Expected: all tests pass.

```bash
git add tools/player_proposal_review.py tools/generate_player_draft.py tests/test_player_proposal_review.py tests/test_generate_player_draft.py
git commit -m "feat(players): generate complete Retro proposals"
```

---

### Task 5: Trusted Proposal Schema and Recomputation

**Files:**
- Modify: `editor/player_spec.py`
- Modify: `tools/generate_player_draft.py`
- Modify: `run.py`
- Modify: `tests/test_player_specs.py`
- Modify: `tests/test_run_pipeline.py`

**Interfaces:**
- Consumes: complete proposal builder and snapshot/resolver/review APIs.
- Produces: `ProposalMetadata`, `load_player_specs(directory: Path | None = None, *, allow_proposals: bool = False) -> tuple[PlayerSpec, ...]`, `validate_generated_proposal(path: Path, edit_file: EditFile) -> Mapping[str, object]`, and proposal-aware `cmd_players_validate`.

- [ ] **Step 1: Write failing exact-schema tests**

Replace incomplete-create draft expectations in `tests/test_player_specs.py`. Add a complete proposal fixture and assert:

```python
spec = load_player_specs(tmp_path, allow_proposals=True)[0]
assert spec.proposal is not None
assert spec.proposal.generator == "pes-retro-mature-proposal-v1"
assert spec.proposal.needs_human_review is True
```

Default loading must raise `PlayerSpecError` with `requires human approval`. Proposal-aware loading must reject old `draft.missing`, nulls, unknown keys, malformed snapshot, operation/mode mismatch, and invalid OVR schema.

- [ ] **Step 2: Run schema tests and verify RED**

Run: `python -m pytest tests/test_player_specs.py -q`

Expected: failures because every draft still routes through `IncompletePlayerSpecError` and `allow_proposals` does not exist.

- [ ] **Step 3: Add immutable proposal metadata and clean parser cutover**

Add:

```python
@dataclass(frozen=True, slots=True)
class ProposalMetadata:
    generator: str
    needs_human_review: bool
    source_snapshot: Mapping[str, object]
    ovr_review: Mapping[str, object]
    issue_number: int
    issue_url: str
    submitted_team: str
```

Add `proposal: ProposalMetadata | None` to `PlayerSpec`. Completed-spec parsing remains exact. Proposal parsing requires complete identity/PES values and the draft-only top-level fields. Delete `_CREATE_DRAFT_MISSING`, `IncompletePlayerSpecError`, and the old partial-draft parser after migrating all callers/tests; leave no compatibility branch.

`load_player_specs(allow_proposals=False)` must reject proposals. `allow_proposals=True` returns them only after strict structural parsing.

- [ ] **Step 4: Write failing offline recomputation tests**

In `tests/test_run_pipeline.py`, materialize one generated proposal and monkeypatch `fetch_pes_retro_stats_profile` to `pytest.fail("validation must not fetch")`. Assert `cmd_players_validate` reports `proposal_ready` and exits normally. Parameterize tampering one source field/hash, converted PES field, allocated ID, patch baseline, OVR value, and canonical order; each must exit `2` with a narrow diagnostic.

- [ ] **Step 5: Run command tests and verify RED**

Run the new named tests only with `python -m pytest tests/test_run_pipeline.py -k proposal -q`.

Expected: failure because validation rejects proposals before loading the base.

- [ ] **Step 6: Implement exact trusted recomputation**

Add `validate_generated_proposal(path, edit_file)` beside the pure generator builder. It must:

1. read the untrusted JSON with bounded existing file behavior;
2. reconstruct the request from validated evidence;
3. reconstruct the source through `profile_from_snapshot`;
4. rerun `map_pes21_proposal`;
5. rerun `build_player_draft` using the verified base and occupied completed IDs;
6. compare the complete expected object with the actual object;
7. report the first canonical JSON path whose value differs, without dumping payloads.

Restructure `cmd_players_validate` to verify/decrypt/load the base before proposal recomputation, load specs with `allow_proposals=True`, report proposal status as `proposal_ready`, and preserve current completed-spec status rules. Validation must not call the fetcher.

- [ ] **Step 7: Run focused schema/command tests and commit**

Run:

`python -m pytest tests/test_player_specs.py tests/test_run_pipeline.py -k "player or proposal" -q`

Expected: all selected tests pass.

```bash
git add editor/player_spec.py tools/generate_player_draft.py run.py tests/test_player_specs.py tests/test_run_pipeline.py
git commit -m "feat(players): validate complete proposals"
```

---

### Task 6: Human Approval Command and Apply Gate

**Files:**
- Modify: `editor/player_spec.py`
- Modify: `run.py`
- Modify: `tests/test_player_specs.py`
- Modify: `tests/test_run_pipeline.py`
- Modify: `tests/test_player_spec_integration.py`

**Interfaces:**
- Consumes: Task 5 proposal parser and `validate_generated_proposal`.
- Produces: `approve_player_proposal(path, edit_file) -> Path`, `cmd_players_approve`, and hard proposal rejection in apply paths.

- [ ] **Step 1: Write failing apply-gate tests**

Assert both direct application and CLI application reject an otherwise valid proposal:

```python
result = apply_player_spec(edit_file, proposal_spec, revision, all_players)
assert (result.status, result.reason) == ("rejected", "human_review_required")
```

Also assert no flag or function parameter can bypass the gate and `edit_file._data` remains byte-identical.

- [ ] **Step 2: Run apply-gate tests and verify RED**

Run: `python -m pytest tests/test_player_specs.py tests/test_run_pipeline.py -k human_review -q`

Expected: failure because proposal metadata is not checked by apply.

- [ ] **Step 3: Implement the hard apply gate**

Return/reject before any identity, roster, entry, output lock, backup, or mutation access when `spec.proposal is not None`. Keep proposal-aware loading exclusive to validation/approval commands; normal apply loading must emit a concise approval-required message.

- [ ] **Step 4: Write failing approval transformation tests**

Create a complete proposal fixture, approve it, and assert:

- output path is unchanged;
- `source` and `draft` are absent;
- evidence keys are exactly profile URL, proof URLs, effective date, and reason;
- reason is exactly `Reviewed automated Pes Retro Stats proposal from issue #123`;
- identity/PES/applies-to values are unchanged;
- output loads with default `load_player_specs`;
- second approval rejects as already approved;
- stale/tampered/conflicting proposal leaves original bytes unchanged;
- temporary-file/open/replace failure cleans up and preserves original;
- successful write uses flush, fsync, and atomic replace.

- [ ] **Step 5: Run approval tests and verify RED**

Run: `python -m pytest tests/test_run_pipeline.py -k approve -q`

Expected: failure because command/function do not exist.

- [ ] **Step 6: Implement atomic approval and CLI registration**

Register:

```python
p_players_approve = players_sub.add_parser(
    "approve", help="Approve one validated Player Update proposal"
)
p_players_approve.add_argument(
    "--spec", required=True, help="Canonical players/<slug>.json proposal path"
)
p_players_approve.set_defaults(func=cmd_players_approve)
```

Validate the path is exactly one direct child of configured `PLAYER_SPECS_DIR`, has canonical slug spelling, and is not a symlink. Rerun base verification and proposal recomputation immediately before transformation. Write canonical `json.dumps(completed, indent=2, sort_keys=True) + "\n"` through an exclusive same-directory temporary file, flush/fsync, and `os.replace`.

- [ ] **Step 7: Add proposal → approval → apply integration tests**

Extend `tests/test_player_spec_integration.py` with one update and one create roundtrip. The create fixture must free one destination roster slot before apply. Assert integrity after save/encrypt/decrypt and assert an unapproved copy cannot mutate.

- [ ] **Step 8: Run focused tests and commit**

Run:

`python -m pytest tests/test_player_specs.py tests/test_run_pipeline.py tests/test_player_spec_integration.py -q`

Expected: all tests pass.

```bash
git add editor/player_spec.py run.py tests/test_player_specs.py tests/test_run_pipeline.py tests/test_player_spec_integration.py
git commit -m "feat(players): add explicit proposal approval"
```

---

### Task 7: Proposal Provenance, Workflow UX, and Roadmap Cleanup

**Files:**
- Create: `tools/check_player_proposal_origin.py`
- Create: `tests/test_player_proposal_origin.py`
- Modify: `.github/workflows/generate-player-update.yml`
- Modify: `.github/workflows/validate-player-update-pr.yml`
- Modify: `tests/test_workflow_config.py`
- Create: `tests/test_readme_roadmap.py`
- Modify: `README.md`, `README.id.md`, `README.es.md`, `README.pt.md`, `README.ar.md`, `README.zh.md`, `README.it.md`, `README.ru.md`, `README.de.md`, `README.fr.md`, `README.tr.md`

**Interfaces:**
- Consumes: validated proposal JSON, event-derived base/head repository names, head ref, proposal-ready exit behavior, and unchanged `SPEC_PATH`/`PLAYER_NAME` machine output.
- Produces: `ProposalOriginError`, `validate_player_proposal_origin(payload, *, base_repo, head_repo, head_ref)`, a trusted origin-check CLI, human-review PR instructions, and documentation with only Local Update/Multi-Base remaining in the roadmap.

- [ ] **Step 1: Write failing origin-policy tests**

Create `tests/test_player_proposal_origin.py` with these contracts:

```python
def test_generated_proposal_requires_matching_same_repository_issue_branch():
    validate_player_proposal_origin(
        generated_payload(issue_number=42),
        base_repo="owner/repo",
        head_repo="owner/repo",
        head_ref="player-draft/issue-42",
    )


@pytest.mark.parametrize(
    ("head_repo", "head_ref", "issue_number"),
    [
        ("fork/repo", "player-draft/issue-42", 42),
        ("owner/repo", "feature/fabricated-proposal", 42),
        ("owner/repo", "player-draft/issue-41", 42),
    ],
)
def test_generated_proposal_rejects_untrusted_origin(
    head_repo, head_ref, issue_number
):
    with pytest.raises(ProposalOriginError):
        validate_player_proposal_origin(
            generated_payload(issue_number=issue_number),
            base_repo="owner/repo",
            head_repo=head_repo,
            head_ref=head_ref,
        )


def test_completed_spec_remains_allowed_from_a_fork():
    validate_player_proposal_origin(
        completed_payload(),
        base_repo="owner/repo",
        head_repo="fork/repo",
        head_ref="player/update",
    )
```

Also reject a source-without-draft shape, draft-without-source shape, wrong generator model, non-positive/bool issue number, mismatched issue URL repository/number, control characters, and noncanonical `owner/repo` or branch spelling.

- [ ] **Step 2: Run origin tests and verify RED**

Run: `python -m pytest tests/test_player_proposal_origin.py -q`

Expected: collection error because `tools.check_player_proposal_origin` does not exist.

- [ ] **Step 3: Implement the trusted origin checker**

`validate_player_proposal_origin` must treat a payload without both `source` and `draft` as a direct completed spec and return. If either draft-only field exists, require both, require generator `pes-retro-mature-proposal-v1`, parse the already schema-bounded evidence issue number/URL, and enforce exact same-repository and `player-draft/issue-<N>` equality. The CLI reads one bounded JSON file and accepts required `--base-repo`, `--head-repo`, and `--head-ref` arguments; it prints no payload content.

- [ ] **Step 4: Write failing workflow-copy/security tests**

Extend `tests/test_workflow_config.py` to assert event parsing emits strictly validated `head_repo` and `head_ref`, then runs the trusted origin checker after materialization and before `python run.py players validate`. Assert the generated PR body contains:

```python
assert "complete and CI-verified" in pr_body
assert "requires explicit human approval" in pr_body
assert "community-weighted estimate, not an official in-game rating" in pr_body
```

Retain exact assertions for `pull_request_target` isolation, `contents: read`, trusted base checkout, advertised head SHA, single materialized JSON, no head checkout, and no head code execution. Assert the generator still emits exactly two machine lines in `SPEC_PATH`, `PLAYER_NAME` order.

- [ ] **Step 5: Run workflow tests and verify RED**

Run: `python -m pytest tests/test_workflow_config.py -q`

Expected: failures because the origin-check step is absent and the current body calls every proposal incomplete.

- [ ] **Step 6: Wire origin enforcement and update review copy**

Extend the trusted event parser to validate and emit `head_repo` and `head_ref` from `pull_request.head.repo.full_name` and `pull_request.head.ref`. After the sole JSON blob is materialized, invoke:

```bash
python tools/check_player_proposal_origin.py \
  --spec "$PLAYER_PATH" \
  --base-repo "$GITHUB_REPOSITORY" \
  --head-repo "$HEAD_REPO" \
  --head-ref "$HEAD_REF"
```

Replace only obsolete incomplete-proposal wording. Keep the PR as `--draft`, preserve permissions, triggers, concurrency, branch naming, file-boundary check, and trusted command execution. Do not add an auto-approval label or merge step.

- [ ] **Step 7: Write failing README roadmap test**

Create `tests/test_readme_roadmap.py` with a parameterized assertion for all 11 README paths. Isolate each roadmap section between its localized roadmap heading and the next same-level heading. Record the current heading counts, badge targets, fenced code blocks, and relative Markdown links as immutable test fixtures before editing. Assert exactly one numbered or bulleted planned item remains, the item still contains that file's localized Local Update/Multi-Base terms, and the section no longer contains both `OVR` and Pes Retro converter wording.

- [ ] **Step 8: Run the README test and verify RED**

Run: `python -m pytest tests/test_readme_roadmap.py -q`

Expected: failure because the completed converter/OVR item still exists.

- [ ] **Step 9: Remove the completed roadmap item in every translation**

Delete only the Pes Retro Stats Converter/OVR Calculator roadmap item from all 11 README files. Change plural lead-ins such as “These items” to singular equivalents where grammatically required. Preserve every badge, command, path, table, callout, code fence, and the Local Update/Multi-Base item.

- [ ] **Step 10: Run origin/workflow/docs tests and commit**

Run:

`python -m pytest tests/test_player_proposal_origin.py tests/test_workflow_config.py tests/test_readme_roadmap.py -q`

Expected: all tests pass.

```bash
git add tools/check_player_proposal_origin.py tests/test_player_proposal_origin.py .github/workflows/generate-player-update.yml .github/workflows/validate-player-update-pr.yml tests/test_workflow_config.py tests/test_readme_roadmap.py README.md README.id.md README.es.md README.pt.md README.ar.md README.zh.md README.it.md README.ru.md README.de.md README.fr.md README.tr.md
git commit -m "ci(players): secure mature proposal reviews"
```

---

### Task 8: End-to-End Contract Verification

**Files:**
- Modify only if a real uncovered contract requires it: `tests/test_player_spec_integration.py`
- No production change is permitted unless a new failing behavioral test is added first.

**Interfaces:**
- Consumes: all prior task interfaces.
- Produces: end-to-end proof for both update and create operations.

- [ ] **Step 1: Add any missing end-to-end fixture coverage**

The integration suite must exercise these two complete flows with no network:

```text
normalized Retro fixture
  -> complete proposal JSON
  -> proposal validation succeeds
  -> apply rejects before approval
  -> approval transformation succeeds
  -> completed spec validation succeeds
  -> apply assessment/update or create succeeds
  -> save integrity survives encrypt/decrypt
```

Update uses Marco Palestra. Create uses Dastan Satpaev with one Chelsea roster slot freed. Assert generated JSON has no null/missing value, completed JSON has no source/draft/OVR metadata, and final decoded gameplay values match the converter proposal.

- [ ] **Step 2: Run the full player-proposal focused suite**

Run:

```bash
python -m pytest -q \
  tests/test_player_ovr.py \
  tests/test_ovr_calc.py \
  tests/test_pes_retro_stats.py \
  tests/test_pes_retro_snapshot.py \
  tests/test_pes21_proposal.py \
  tests/test_player_proposal_resolution.py \
  tests/test_player_proposal_review.py \
  tests/test_player_draft_diff.py \
  tests/test_generate_player_draft.py \
  tests/test_player_specs.py \
  tests/test_run_pipeline.py \
  tests/test_player_spec_integration.py \
  tests/test_player_proposal_origin.py \
  tests/test_workflow_config.py \
  tests/test_readme_roadmap.py
```

Expected: all tests pass, zero failures.

- [ ] **Step 3: Run current repository Player Update validation**

Run: `python run.py players validate`

Expected: exit `0`; Dastan remains `waiting (destination_roster_full)` and Marco is `ready (all_current)` until their current completed specs are changed separately.

- [ ] **Step 4: Smoke the shared OVR CLI**

Run:

`PYTHONPATH=. python tools/ovr_calc.py 162196 --spec players/marco-palestra.json --position RB`

Expected: exit `0`, one-decimal base/proposal OVR output, no missing-ability fallback warning, and no network access.

- [ ] **Step 5: Run static workflow loading**

Run:

```bash
ruby -e 'require "yaml"; ARGV.each { |p| YAML.safe_load(File.read(p), permitted_classes: [], permitted_symbols: [], aliases: false) }' \
  .github/workflows/generate-player-update.yml \
  .github/workflows/validate-player-update-pr.yml \
  .github/workflows/ci.yml
```

Expected: exit `0` with no YAML parse errors.

- [ ] **Step 6: Commit integration-only test additions if any**

If Step 1 changed a test file:

```bash
git add tests/test_player_spec_integration.py
git commit -m "test(players): cover mature proposal roundtrips"
```

If no file changed, do not create an empty commit.
