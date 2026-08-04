# Community Player Spec Workflow Design

## Goal

Replace the automatic curated-player pilot with a community-driven, reviewable player-spec workflow. Each approved player has one JSON file. SortitoutSI supplies draft metadata and provenance; contributors and maintainers remain responsible for final PES values.

The repository must still be able to create a player without the in-game editor. Binary serialization remains a generic engine, but the daily transfer command must not load or apply player specs implicitly.

## Decisions

- Store one file per player under `players/<canonical-slug>.json`.
- Use one schema with `operation: "create"` or `operation: "update"`.
- Treat merge approval as the approval state; do not store an `approved` flag.
- Scope every spec to an exact bundled-base revision.
- Keep superseded specs as history; never require deletion after an official patch.
- Generate drafts from a community issue only after a maintainer applies the `generate-player-draft` label.
- Continue to accept direct one-player pull requests from advanced contributors.
- Apply merged specs only through the explicit `players apply` command used by GitHub Actions.
- Permit existing-player updates only as explicit PES field patches with expected current values.
- Never convert Football Manager CA or attributes directly into PES abilities.

## Revert Boundary

Remove the current pilot integration:

- `data/curated_players.json` and its project configuration entry;
- Dastan-specific bundled-manifest behavior;
- automatic curated-player loading, planning, waiting, creation, and audit wiring from `run`;
- the monolithic manifest loader and club allowlist model.

Retain and generalize the proven binary primitives:

- `editor/player_codec.py` bitfield decoding, bit-preserving patching, player serialization, and generic appearance serialization;
- `EditFile.append_created_player` duplicate, capacity, and slot checks;
- atomic create/apply behavior and post-write integrity validation;
- created-player audit/report rendering for the explicit apply command.

The curated domain model becomes a neutral player-spec model. The generic serializer depends on a structural player-record interface rather than a curated-manifest type.

## Repository Structure

```text
players/
├── dastan-satpayev.json
└── marco-palestra.json

data/
└── base_manifest.json

editor/
├── player_codec.py
└── player_spec.py

scraper/
└── sortitoutsi_player.py

.github/
├── ISSUE_TEMPLATE/player-spec.yml
└── workflows/
    └── generate-player-spec.yml
```

`editor/player_spec.py` owns schema loading, semantic validation, deterministic ordering, lifecycle assessment, create/update assessment, and atomic application. `scraper/sortitoutsi_player.py` owns only SortitoutSI profile retrieval and draft extraction. GitHub-specific event parsing remains in a small workflow entry point rather than the binary editor.

## Base Revision and Spec Lifecycle

`data/base_manifest.json` identifies the bundled base with a stable revision name and SHA-256 digest. CI verifies that the checked-in `base/EDIT00000000` matches that manifest. A player spec declares compatible revision names in `applies_to`.

When an official patch replaces the bundled base:

1. the base file and `base_manifest.json` receive a new revision and digest;
2. specs that do not include the new revision become `needs_review` and are skipped automatically;
3. the files remain in the repository as provenance;
4. if upstream now contains the player or corrected stats, a maintainer marks the spec `upstreamed` with the new revision and evidence;
5. if the problem remains, a maintainer updates the same player file with the new baseline and compatible revision.

Git history preserves earlier values. No spec is deleted merely because an official patch supersedes it. Old specs can never overwrite a newer official base unless a maintainer explicitly revalidates and extends their compatibility.

Lifecycle values are `active`, `upstreamed`, and `retired`. Only `active` specs compatible with the selected base revision can mutate a save. `upstreamed`, `retired`, and `needs_review` specs are report-only.

The workflow verifies the pristine base digest before making an output copy. After verified transfers alter that copy, `players apply` receives the already-verified base revision explicitly; it does not compare the modified output's digest to the pristine digest.

## Player Spec Schema

Every file contains:

- `schema_version`: initially `1`;
- `operation`: `create` or `update`;
- `lifecycle`: status, reason, and optional superseding base revision;
- `applies_to`: exact bundled-base revisions reviewed for this spec;
- `identity`: canonical name, aliases, PES ID, and SortitoutSI ID;
- `evidence`: profile URL, proof URLs, and effective date;
- `pes`: final PES data or explicit patches.

The filename equals the normalized canonical-name slug. PES IDs, SortitoutSI IDs, and normalized aliases are unique across all specs.

### Create

A create spec requires a complete binary-safe PES record:

- canonical and print names;
- nationality, age, height, and weight;
- registered and playable positions;
- playing style, strong/weak foot settings, form, and injury resistance;
- all 25 supported ability fields;
- player skills and COM styles;
- generic appearance inputs;
- destination team ID and canonical team name;
- optional preferred shirt number.

The engine rejects incomplete records. It does not infer missing PES values from CA.

The initial create example is `players/dastan-satpayev.json`. The current bundled base was inspected and contains 29,503 catalogued players with no Dastan Satpayev identity match. Dastan uses PES ID `200000` and SortitoutSI ID `2000370206`. His complete PES values and evidence from the pilot migrate into the per-player file. If Chelsea remains at 40 players, apply returns `waiting` and does not release another player.

If a later official base contains Dastan, the old create spec cannot update or overwrite that official record. It is skipped because its base revision is no longer compatible, then marked `upstreamed` after review.

### Update

An update spec targets an existing PES ID and contains only whitelisted patches. Every patch carries an expected current value and a final value:

```json
{
  "schema_version": 1,
  "operation": "update",
  "lifecycle": {"status": "active"},
  "applies_to": ["fl26-u2.2-national-squads"],
  "identity": {
    "name": "Marco Palestra",
    "pes_id": 162196,
    "sortitoutsi_id": 2000136198
  },
  "pes": {
    "abilities": {
      "speed": {"from": 77, "to": 80},
      "acceleration": {"from": 75, "to": 77},
      "defensive_awareness": {"from": 61, "to": 62},
      "ball_winning": {"from": 59, "to": 60}
    }
  }
}
```

The current bundled base confirms Marco Palestra at PES ID `162196`, registered position `RB`, with the four listed baseline values. The catalog's `overall_rating` value is `0` and is not authoritative, so the spec never writes a synthetic OVR.

Phase-one update specs may patch supported ability fields, playable-position proficiency, playing style, player skills, COM styles, and reviewed basic physical fields. They may not rename the player, change identity IDs, change appearance, move the player, or alter the shirt number. Existing unknown bits and unrequested fields remain byte-for-byte unchanged.

If a later official patch changes Marco, the old revision-scoped update is skipped before field assessment. A maintainer either marks it `upstreamed` or revalidates a new explicit `from`/`to` patch for the new base.

## Contribution Flow

1. A community member opens the structured player-spec issue, selects create or update, and supplies a SortitoutSI URL, current team, effective date, and proof URLs.
2. A maintainer verifies that the request is in scope and applies `generate-player-draft`.
3. `generate-player-spec.yml` runs from trusted default-branch code, reads the GitHub event JSON, validates the SortitoutSI host and numeric ID, fetches draft metadata, and opens a bot-owned draft PR.
4. The PR contains one `players/<slug>.json` file. Unknown PES decisions remain visibly incomplete, so CI fails until a human supplies and reviews them.
5. The issue author provides corrections through PR discussion; maintainers finalize the bot branch. Advanced contributors may instead submit a direct one-file PR.
6. CI validates the file and exercises it against a temporary copy of the bundled base.
7. Merge is the only approval signal.

The workflow uses `issues: read`, `contents: write`, and `pull-requests: write` only. It triggers on the maintainer-controlled label, uses concurrency keyed by issue number, and never interpolates issue text into shell commands. The generator fetches only allowlisted SortitoutSI URLs. Proof URLs are recorded but not fetched, preventing SSRF through community input.

## Explicit Application Flow

The CLI exposes a dedicated command family:

```bash
python run.py players validate
python run.py players apply --base-revision fl26-u2.2-national-squads \
  --edit-file output/EDIT00000000 --in-place
```

The daily workflow verifies the pristine base, prepares an output copy, applies verified transfers, then runs `players apply` explicitly. Applying player specs after transfers allows a verified departure to free a roster slot before a create spec is assessed. The transfer command itself remains unaware of player specs.

Specs are processed in filename order. Each returns one status:

- `created` or `updated`;
- `already_applied`;
- `waiting`, such as a full destination roster;
- `conflict`, when an update baseline no longer matches;
- `needs_review`, when the official base revision changed;
- `upstreamed` or `retired` for inactive historical specs;
- `rejected`, for schema, identity, ID, range, or binary-safety failures.

A non-applicable spec does not block independent specs. Each player mutation is atomic. After all applicable specs run, the save is written only if at least one mutation succeeded. The complete output then passes integrity validation and an encrypt/decrypt round trip before audit records and reports persist.

Create idempotency uses PES ID plus normalized identities. An existing matching player on a compatible base returns `already_applied`; an ID belonging to another identity is `rejected`. Update idempotency follows three-way semantics: current equals `from` applies the patch, current equals `to` is already applied, and any third value is a conflict.

## CI Validation

Community player PRs modify exactly one file under `players/` and no executable project code. CI performs:

- JSON syntax and schema validation;
- filename/canonical-name slug agreement;
- lifecycle and base-revision validation;
- supported field and numeric range validation;
- global PES ID, SortitoutSI ID, and normalized-alias uniqueness checks;
- create completeness and destination-team validation;
- create identity absence against the current bundled base when its revision is listed;
- update target presence and expected-baseline validation;
- serializer encode/decode checks for create specs;
- bit-preservation checks for update specs;
- atomic application on a temporary decrypted base;
- save integrity validation and Linux encrypt/decrypt round trip.

A bundled-base update is allowed to leave older specs in `needs_review`; it does not require deleting or immediately rewriting every historical file. Code-changing maintainer PRs continue through the normal full test matrix.

## Error Handling

- SortitoutSI unavailable: generator fails without creating or modifying a spec.
- Incomplete SortitoutSI profile: generator may open a draft only when identity is unambiguous; missing PES fields remain invalid until reviewed.
- Duplicate or ambiguous identity: CI rejects the spec.
- Concurrent draft generation for one issue: workflow concurrency prevents duplicate bot PRs.
- Full roster: create reports `waiting`; no automatic release is permitted.
- Stale existing-player baseline on a compatible base: update reports `conflict`; no field changes.
- Incompatible official base revision: spec reports `needs_review`; no field assessment or mutation occurs.
- Failed player mutation: that player's bytes are restored.
- Failed final integrity or encryption round trip: no output audit is committed and the generated artifact is rejected.

## Testing Strategy

Unit tests cover schema variants, URL/event parsing, slugging, ID and alias uniqueness, lifecycle transitions, base compatibility, complete-create requirements, update three-way semantics, range checks, and deterministic ordering.

Binary tests defend serializer round trips, generic appearance linkage, duplicate-ID rejection, capacity checks, and preservation of all unrequested bits.

Integration tests use the bundled base to prove:

- Dastan is absent and a full Chelsea roster yields `waiting` without mutation;
- Dastan is created, registered, validated, encrypted, and reopened when a temporary slot is available;
- a later base containing Dastan leaves the old create spec inactive and preserves the official record;
- Marco's four patches apply only from the expected baseline;
- rerunning both active compatible specs is idempotent;
- a changed Marco baseline yields `conflict` without modification;
- a new base revision yields `needs_review` before applying Marco's old patch;
- one waiting, conflicting, or inactive spec does not prevent an independent valid spec;
- audit JSONL and visual reports are written only after successful output verification.

Workflow tests feed representative issue event JSON into the generator script without granting network or repository write access. GitHub Actions YAML remains thin orchestration over tested Python entry points.

## Non-goals

- Automatic FM CA-to-PES conversion.
- Community-controlled workflow execution or repository write tokens.
- Automatic player release to create roster capacity.
- Face/hair recreation from external sources.
- Existing-player transfers, renames, appearance edits, or shirt-number edits through player specs.
- Applying player specs implicitly during the transfer command.
- Forcing old community values onto a newly updated official base.
- Deleting historical specs solely because upstream superseded them.

## Acceptance Criteria

- The current monolithic curated pilot and automatic `run` integration are removed.
- Generic binary create/update primitives remain covered and reusable.
- Dastan and Marco exist as independent, validated example specs.
- A maintainer-labeled issue can produce one bot draft PR.
- Direct one-player PRs pass through the same validator.
- The explicit workflow command can create Dastan when safe and patch Marco only from the reviewed baseline.
- Replacing the bundled base automatically makes unreviewed old specs inactive without deleting them.
- Daily transfer behavior remains unchanged unless the workflow explicitly invokes player-spec application.
