# Pes Retro Stats Mature Player Proposals Design

**Date:** 2026-08-06  
**Status:** Approved for implementation planning

## Problem

The repository already has a secure Pes Retro Stats fetcher, a PES 2021 attribute converter, an update-diff generator, and a standalone position-weighted OVR prototype. The current generator still produces incomplete create drafts whose internal PES fields must be filled manually. The OVR engine is not part of the trusted proposal workflow. Maintainers therefore need game-internal knowledge to complete a new player, and the generated file does not provide a reproducible OVR review.

The desired product is one end-to-end workflow: give it a canonical Pes Retro Stats profile and review evidence, then receive a complete update or create proposal that CI can reproduce and a human can approve without entering raw PES IDs or editing JSON structure.

The Pes Retro Stats converter and the OVR calculator have different roles. The converter determines PES 2021 field proposals from source data. OVR is derived review information calculated after conversion; it must never determine the proposed abilities or become a serialized save field.

## Goals

- Automatically fetch and normalize one canonical Pes Retro Stats profile.
- Convert the normalized source into a complete PES 2021 proposal.
- For updates, produce an exact base-to-proposal patch and OVR comparison.
- For creates, resolve every game-internal field automatically and produce proposal-only OVR.
- Require no maintainer-entered PES player, team, or nationality IDs.
- Produce no `null` values or `draft.missing` fields.
- Let trusted CI reproduce every generated value without refetching the web.
- Let complete proposals pass CI while remaining impossible to apply before explicit human approval.
- Provide one approval command that converts a validated proposal into a completed active player specification without manual JSON surgery.
- Preserve the one-player-file PR boundary and trusted-code-only security model.

## Non-goals

- Do not auto-approve, auto-merge, or auto-apply generated proposals.
- Do not use OVR to choose abilities, release roster players, or make transfer decisions.
- Do not claim the estimate is the official in-game PES 2021 overall rating.
- Do not infer real appearance from Pes Retro Stats; new players receive a deterministic generic appearance.
- Do not change existing player appearance during an update.
- Do not run contributor code or expand workflow permissions.
- Do not retain normalized source snapshots or OVR metadata after approval.
- Do not support a target revision whose base, nationality map, or team catalog is not validated.

## Current Context

- `scraper/pes_retro_stats.py` fetches and normalizes an allowlisted profile.
- `scraper/pes21_proposal.py` maps a complete source profile to an immutable PES 2021 proposal.
- `tools/player_draft_diff.py` resolves an existing base player and emits update patches.
- `tools/generate_player_draft.py` builds update drafts and partial create drafts.
- `editor/player_spec.py` strictly validates generated and completed specification shapes.
- `tools/ovr_calc.py` owns a prototype float weight table and silently substitutes ability value `40` when a field is absent.
- `validate-player-update-pr.yml` materializes only one validated player JSON path from an untrusted PR head and executes trusted base code.

## End-to-End Flow

```text
Issue form + canonical Pes Retro Stats URL
  -> trusted bounded fetch
  -> normalized source snapshot
  -> PES 2021 converter
  -> verified-base identity and internal-field resolution
  -> position-weighted OVR review
  -> complete one-file proposal
  -> trusted CI recomputation
  -> explicit human review
  -> atomic approval conversion
  -> completed active player specification
```

The issue form continues to provide operation, canonical player name, canonical Pes Retro Stats profile URL, current team, effective date, proof URLs, and the existing confirmations. Contributors and maintainers never provide raw game IDs.

## Normalized Source Snapshot

Generated proposals contain a draft-only `source` snapshot sufficient to rerun conversion without network access. It includes:

- source model identifier `pes-retro-normalized-v1`;
- canonical profile UUID and URL;
- canonical player name and optional full name;
- birth date, nationality, current club, shirt number, registered position, foot, form, injury tolerance, and playing style;
- the complete source ability-stat map;
- the complete source position-grade map;
- source player skills and COM playing styles;
- `snapshot_sha256`, computed over canonical UTF-8 JSON of the normalized snapshot excluding the hash field.

No fetched HTML, Next.js payload, response headers, or runtime fetch timestamp is stored. Equal source data produces byte-identical snapshot JSON. `snapshot_sha256` proves internal integrity, not web authenticity. CI validates the snapshot schema and hash, then reconstructs `PesRetroStatsProfile` and reruns the trusted converter without refetching the live profile. Source provenance is enforced separately by the same-repository generator-branch gate described below.

The canonical profile URL remains in completed `evidence`; the source snapshot is removed during approval.

## Proposal Conversion

### Existing-player update

1. Verify the target base digest and revision.
2. Resolve one exact canonical team from the submitted current-team name and source club.
3. Resolve one exact rostered player by normalized canonical identity.
4. Decode the complete current PES profile.
5. Convert the normalized Retro snapshot with `map_pes21_proposal`.
6. Emit `from` to `to` patches only for fields whose values differ.
7. Reject a proposal with no changes.
8. Calculate OVR for the proposed relevant positions using both current and proposed ability maps.
9. Preserve every existing appearance byte.

The update proposal contains no missing values. Its identity and all patch baselines come from the verified base, not contributor input.

### New-player create

Create generation also loads the verified base. It converts all source gameplay data and resolves the remaining internal fields as follows.

#### Team

Resolve the submitted current-team name and source-club name through canonical normalized team names plus the repository's validated alias data. Both names must resolve uniquely to the same team ID and canonical name. Fuzzy-only, ambiguous, rosterless, or conflicting matches fail closed with the human-readable club names.

#### Nationality

Add `data/pes21_nationalities.json`, versioned for the target PES 2021 data model. Each record contains one numeric PES nationality ID, one canonical source name, and normalized aliases. Startup validation rejects duplicate IDs, duplicate normalized names or aliases, empty values, and IDs outside the codec width. The source nationality must resolve uniquely. Unknown names fail with the source name and mapping version; the workflow never asks for a numeric ID.

#### Player ID

Reserve IDs `200000..299999` for repository-created players. Derive the initial slot from the canonical Pes Retro Stats UUID:

```text
seed = first_8_bytes_of_sha256(uuid_utf8) interpreted as unsigned big-endian
initial_id = 200000 + (seed % 100000)
```

Probe the range circularly in ascending order from `initial_id`. A slot is unavailable if it occurs in the verified base, any completed player specification, or the proposal being validated. The first free slot is selected. Exhaustion fails closed. Identity and PES `player_id` must match. CI recomputes the same allocation. A later merge that occupies the selected ID makes a stale proposal fail until regenerated.

#### Print name

Normalize the canonical player name to Unicode NFC and collapse whitespace. Take the final whitespace-delimited token, remove leading and trailing Unicode punctuation, and uppercase it. If that is empty, use the normalized full player name. Encode with the existing PES UTF-8 boundary rules; reject rather than silently truncate when the result exceeds 60 bytes. The generated value remains visible to the human reviewer but is never missing.

#### Generic appearance

Updates never change appearance. Creates use generic physique, face, and hair serialization already provided by `editor.player_codec`. Choose `skin_color` and `iris_color` deterministically from this versioned known-good palette observed in the bundled validated base:

```text
appearance-palette-v1 =
  (3,17), (2,17), (4,17), (1,17),
  (5,17), (12,16), (30,17), (9,17)
```

Select `palette[sha256(uuid_utf8)[8] % len(palette)]`. The choice is generic, not a claim about the real player. It is stable across reruns and uses no runtime RNG.

#### Completed create payload

The create payload contains player ID, canonical name, print name, team ID and name, optional validated shirt number, nationality ID, all converted gameplay fields, and deterministic generic appearance. It contains no `null` or missing fields.

## Shared OVR Engine

Create `editor/player_ovr.py` as the sole owner of weights and calculation rules.

Public API:

```text
PlayerOvrError(ValueError)
calculate_ovr_tenths(abilities: Mapping[str, int], position: str) -> int
relevant_ovr_positions(
    registered_position: str,
    position_proficiency: Mapping[str, int],
) -> tuple[str, ...]
```

Use integer weights for the shared calculation. The RB column currently contains only the published major weights; unspecified RB attributes remain zero until the complete formula is available. For one position, `weighted_sum` is the sum of each integer weight multiplied by its ability value, and `total_weight` is the sum of those integer weights. Use exact integer half-up rounding:

```text
ovr_tenths = (weighted_sum * 20 + total_weight) // (2 * total_weight)
```

This is half-up rounding of `weighted_sum * 10 / total_weight`. Binary floats and Python's bankers-rounding `round()` are forbidden.

The calculator requires exactly `ABILITY_FIELDS`. Each value must be an integer, not a bool, in `40..99`. Missing or extra fields, invalid values, unknown positions, and zero total weight raise `PlayerOvrError`.

Relevant positions are the union of the converted proposal's registered position and every supported position whose proposal proficiency is greater than zero. Return them once each in canonical `POSITION_NAMES` order.

`tools/ovr_calc.py` becomes a thin CLI over the shared engine. It preserves base-only, `--spec`, and `--position` use, renders integer tenths as one decimal place, and removes copied weights and the missing-ability fallback.

## Proposal Schema

A generated proposal contains all completed gameplay data plus draft-only provenance and review metadata.

The exact draft object is:

```json
{
  "generator": "pes-retro-mature-proposal-v1",
  "needs_human_review": true,
  "ovr_review": {
    "model": "pes2021-community-estimate-v2",
    "mode": "comparison",
    "positions": []
  }
}
```

There is no `missing` field.

### Update OVR row

```json
{
  "position": "RB",
  "base_tenths": 700,
  "proposal_tenths": 812,
  "delta_tenths": 112
}
```

### Create OVR row

```json
{
  "position": "CF",
  "proposal_tenths": 781
}
```

Schema rules:

- `generator` is exactly `pes-retro-mature-proposal-v1`.
- `needs_human_review` is exactly `true`.
- OVR review keys are exactly `model`, `mode`, and `positions`.
- OVR model is exactly `pes2021-community-estimate-v2`; it remains a community estimate, not the official Konami formula.
- Update mode is `comparison`; create mode is `new_player`.
- Positions are non-empty, duplicate-free, canonically ordered, and exactly equal to the relevant proposal positions.
- OVR values are integers, not bools, in `400..990`.
- Update rows have exactly position, base, proposal, and delta; delta equals proposal minus base and is in `-590..590`.
- Create rows have exactly position and proposal; base and delta are forbidden.
- Unknown keys are rejected at every level.

Draft evidence retains issue number, issue URL, submitted current team, canonical profile URL, proof URLs, and effective date. All generated proposal data remains reviewable in the single player JSON file.

## Validation and Human Approval

### Proposal validation

`python run.py players validate` recognizes a structurally complete generated proposal and performs all checks before returning success:

1. verify the base digest and revision;
2. validate and hash the normalized source snapshot;
3. reconstruct the source profile and rerun the converter;
4. rerun update resolution or create internal-field resolution;
5. compare every generated gameplay field or patch with the recomputed result;
6. recompute relevant positions and OVR review;
7. run normal semantic applicability checks against the base;
8. report `proposal_ready` and exit `0` when everything matches.

A complete proposal may validly report `waiting` for an environmental apply condition such as a full destination roster. That does not make its data incomplete.

CI success means the proposal is complete, internally consistent, reproducible, semantically safe to review, and admitted by the workflow provenance gate. It does not mean approved.

### Apply gate

`players apply` rejects every specification containing `draft.needs_human_review=true`, even if validation passes. No command flag bypasses this gate.

### Approval command

Add:

```bash
python run.py players approve --spec players/<canonical-slug>.json
```

The command:

1. accepts exactly one canonical player path under `players/`;
2. reruns complete proposal validation against the verified base;
3. refuses an already approved, stale, malformed, conflicting, or non-proposal file;
4. removes the normalized source snapshot and complete `draft` object;
5. converts draft evidence to the existing completed evidence shape;
6. sets reason to `Reviewed automated Pes Retro Stats proposal from issue #<number>`;
7. preserves profile URL, proof URLs, effective date, identity, applies-to revision, and every proposed gameplay value;
8. writes the completed specification with exclusive temporary-file creation, flush, fsync, and atomic replace;
9. prints the approved canonical path and no untrusted multiline content.

Human invocation after review is the approval action. The command does not commit, push, merge, or apply the player.

## Workflow and Security

The generator workflow continues to run only for the exact maintainer-applied label and writes one canonical player JSON to a dedicated issue branch. The pull request remains a GitHub draft. Its body states:

- the proposal is complete and CI-verifiable but requires human approval;
- OVR values are community-weighted estimates, not official game ratings;
- reviewers must inspect converted attributes, internal resolution, and `draft.ovr_review`.

The PR validator continues to check out trusted base code, fetch only the advertised head object, enforce the one-player-file boundary, and materialize only the validated JSON blob. It does not execute head code, refetch the source, expose secrets, or gain new permissions.

Any JSON containing `draft.generator` is accepted only when all of these event-derived conditions hold before semantic validation:

- the pull-request head repository exactly equals the base repository;
- the head ref is exactly `player-draft/issue-<positive-integer>`;
- that integer equals the validated `evidence.issue_number`;
- the validated issue URL names the same repository and issue number.

A generated-proposal-shaped JSON from a fork, arbitrary branch, or mismatched issue is rejected. Direct one-file contribution PRs remain supported only as completed specifications without `source` or `draft` metadata. The snapshot hash then provides reproducible integrity inside the trusted generator branch; it is not treated as a signature.

Unknown or malformed source data, aliases, mappings, IDs, patches, and OVR values fail closed. Errors name the first invalid human-readable field or position and never include fetched HTML, issue-body dumps, decrypted bytes, or other large untrusted payloads.

## Testing

### Source snapshot and converter

- Exact normalized snapshot schema and canonical hash.
- Roundtrip reconstruction of `PesRetroStatsProfile`.
- Rejection of missing, extra, malformed, reordered where order is canonical, or tampered source fields.
- Converter recomputation detects a changed proposal field.
- CI path performs no network access.

### Internal resolution

- Unique canonical and alias team resolution; ambiguous, conflicting, fuzzy-only, rosterless, and missing teams fail.
- Nationality-map schema, alias normalization, duplicate rejection, codec range, known names, and unknown-name error.
- Exact UUID-derived player-ID seed, circular probing, base/spec collisions, stale concurrent proposal collision, and range exhaustion.
- Print-name normalization, punctuation, mononym, Unicode, empty-result fallback, and UTF-8 byte boundary.
- Exact appearance palette selection for fixed UUIDs and rerun stability.

### OVR

- Independently derived known vectors for all 13 positions.
- Exact half-up rounding and canonical relevant-position order.
- Complete ability-map enforcement and invalid position/value failures.
- Update base/proposal/delta and create proposal-only review.
- Rejection after tampering any OVR field, row key, position, or order.

### Generator, validation, and approval

- Update URL fixture produces a complete patch with no missing fields.
- Create URL fixture produces a complete player with no null fields or raw-ID input.
- No-change update aborts without creating a proposal.
- Complete proposals report `proposal_ready` and exit zero.
- `players apply` rejects an unapproved complete proposal.
- `players approve` converts a validated proposal atomically and is non-idempotent.
- Approved output passes completed-spec validation and normal apply semantics.
- Inconsistent tampering of snapshot, converted field, internal ID, base patch, or OVR review blocks validation and approval; coordinated generated-proposal fabrication from a fork or arbitrary branch is blocked by the workflow provenance gate.
- Existing completed specifications continue to validate and apply unchanged.

### Workflow

- Exact label, permissions, branch, one-file boundary, trusted checkout, and no-head-code invariants remain enforced.
- Same-repository generator origin is required only for proposal metadata; fork/direct completed-spec PRs remain supported.
- Fork proposal, arbitrary same-repository branch, issue-number mismatch, and issue-URL mismatch are rejected before semantic validation.
- Draft PR body contains the human-approval and OVR-estimate notices.
- Machine output remains exactly `SPEC_PATH` then `PLAYER_NAME`.

## Acceptance Criteria

1. A canonical Retro profile for an existing player produces a complete revision proposal with exact base patches and relevant-position OVR comparison.
2. A canonical Retro profile for a new player produces a complete create proposal with automatically resolved player, team, and nationality IDs, deterministic print name and generic appearance, and proposal OVR.
3. Neither operation requires a maintainer to enter or infer a raw PES ID.
4. Generated proposals contain no `null`, no `missing`, and no unresolved placeholder.
5. Trusted CI reproduces source conversion, internal resolution, and OVR without network access, detects internal inconsistencies, and accepts generator metadata only from the matching same-repository issue branch.
6. Complete unapproved proposals pass validation but cannot be applied.
7. Explicit human approval converts a proposal into the existing completed-spec format without manual JSON editing.
8. OVR weights and formula exist only in `editor/player_ovr.py`; OVR never becomes a save field.
9. The single-file, trusted-code-only PR security boundary and human merge gate remain intact.
10. Focused source, converter, resolver, OVR, generator, validator, approval, workflow, and integration tests pass.
11. `python run.py players validate` exits zero for the repository's completed player specs.
12. A smoke generation from fixtures, approval, and apply assessment completes end to end for both update and create operations.
