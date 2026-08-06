# Pes Retro Stats OVR Review Integration Design

**Date:** 2026-08-06  
**Status:** Approved for implementation planning

## Problem

The Pes Retro Stats adapter, PES 2021 proposal mapper, and player-draft generator are already integrated. The position-weighted OVR calculator remains a standalone prototype in `tools/ovr_calc.py`. Its weights, permissive fallback behavior, and float output are not a trusted workflow contract, and generated Player Update drafts do not show reviewers how a proposal changes estimated OVR.

OVR is derived review information. It is not a serialized PES field and must not become a second source of truth in completed player specifications or save files.

## Goals

- Move the OVR formula into one production module shared by the generator and CLI.
- Add deterministic OVR review metadata to generated update and create drafts.
- Show update base, proposal, and delta values for relevant positions.
- Show proposal-only values for new players.
- Recompute and validate draft OVR metadata with trusted base code in CI.
- Preserve the one-player-JSON PR boundary and existing human-approval lifecycle.
- Keep completed player specifications and save mutations free of OVR metadata.

## Non-goals

- Do not use OVR to release players from full rosters or make transfer decisions.
- Do not claim the estimate is the official in-game PES 2021 overall rating.
- Do not fetch additional network data.
- Do not change workflow permissions or allow contributor code execution.
- Do not retain OVR metadata after a draft becomes an approved completed specification.
- Do not support positions outside `editor.player_codec.POSITION_NAMES`.

## Current Context

- `scraper/pes_retro_stats.py` securely fetches and normalizes one Pes Retro Stats profile.
- `scraper/pes21_proposal.py` maps the complete profile to an immutable PES 2021 proposal.
- `tools/generate_player_draft.py` builds schema-v2 create proposals or revision-scoped update diffs.
- `editor/player_spec.py` strictly validates generated draft and completed specification shapes.
- `tools/ovr_calc.py` currently owns the only OVR weight table and silently substitutes ability value `40` when a field is absent.
- `validate-player-update-pr.yml` materializes only the validated player JSON from an untrusted PR head and runs trusted base code.

## Architecture

### Shared OVR engine

Create `editor/player_ovr.py` as the only owner of OVR weights and calculation rules.

Public API:

```text
PlayerOvrError(ValueError)
calculate_ovr_tenths(abilities: Mapping[str, int], position: str) -> int
relevant_ovr_positions(
    registered_position: str,
    position_proficiency: Mapping[str, int],
) -> tuple[str, ...]
```

The existing two-decimal weight values become integer hundredths. Calculation uses integer arithmetic and explicit half-up rounding:
For one position, `weighted_sum` is the sum of each integer weight multiplied
by its ability value, and `total_weight` is the sum of those integer weights.

```text
ovr_tenths = (weighted_sum * 20 + total_weight) // (2 * total_weight)
```

This is exact integer half-up rounding of
`weighted_sum * 10 / total_weight`. It must not use binary float behavior or
Python's bankers-rounding `round()`.

`calculate_ovr_tenths` requires exactly `ABILITY_FIELDS`. Every value must be an integer, not a bool, in the inclusive range `40..99`. Unknown positions, missing or extra abilities, invalid values, and a zero total weight raise `PlayerOvrError`.

`relevant_ovr_positions` returns the union of the proposal's registered position and every supported position whose proficiency is greater than zero. Results use canonical `POSITION_NAMES` order and contain no duplicates.

### Draft generator

`tools/generate_player_draft.py` continues to own player-draft assembly.

For an update:

1. Resolve the exact player against the verified base.
2. Decode the complete base ability and position state.
3. Apply proposal targets to derive the complete proposed state.
4. Derive relevant positions from the proposed registered position and proficiencies.
5. Calculate base and proposal OVR for every relevant position.
6. Store base, proposal, and delta tenths in `draft.ovr_review`.

For a create:

1. Use the complete mapped proposal abilities and positions.
2. Derive relevant positions from the proposal.
3. Calculate proposal OVR for every relevant position.
4. Store proposal-only tenths with mode `new_player`.

OVR calculation failure aborts draft generation before any branch or pull request is created.

### CLI

`tools/ovr_calc.py` becomes a thin presentation adapter over `editor.player_ovr`:

- preserve base-only, `--spec`, and `--position` behavior;
- load complete abilities through existing codec/spec APIs;
- render tenths as one decimal place;
- remove the copied weights and permissive missing-ability fallback;
- surface `PlayerOvrError` as a concise CLI failure.

## Draft Schema

`draft.ovr_review` is mandatory for generated drafts.

Update example:

```json
{
  "model": "pes2021-community-estimate-v1",
  "mode": "comparison",
  "positions": [
    {
      "position": "RB",
      "base_tenths": 700,
      "proposal_tenths": 812,
      "delta_tenths": 112
    }
  ]
}
```

Create example:

```json
{
  "model": "pes2021-community-estimate-v1",
  "mode": "new_player",
  "positions": [
    {
      "position": "CF",
      "proposal_tenths": 781
    }
  ]
}
```

Schema rules:

- Review root keys are exactly `model`, `mode`, and `positions`.
- `model` is exactly `pes2021-community-estimate-v1`.
- Update mode is exactly `comparison`; create mode is exactly `new_player`.
- `positions` is non-empty, duplicate-free, canonically ordered, and exactly matches the relevant proposal positions.
- OVR values are integers, not bools, in `400..990`.
- Comparison rows contain exactly `position`, `base_tenths`, `proposal_tenths`, and `delta_tenths`.
- `delta_tenths` exactly equals `proposal_tenths - base_tenths` and is in `-590..590`.
- New-player rows contain exactly `position` and `proposal_tenths`; base and delta fields are forbidden.
- Unknown keys are rejected at every level.

The metadata remains inside `draft`; completed specs must remove both `source` and `draft` under the existing lifecycle.

## Trusted Validation and CI

`editor.player_spec` validates OVR review structure while validating the rest of a generated draft.

`python run.py players validate` must validate OVR before reporting that a generated draft is incomplete:

1. Parse the generated draft with trusted code.
2. Verify the bundled base digest and requested revision.
3. Decrypt and load the trusted base.
4. For update drafts, find the exact player by the already-validated PES identity and reconstruct the proposed abilities and positions from base values plus draft patches.
5. For create drafts, use the complete proposed abilities and positions directly.
6. Recompute the expected OVR review.
7. Compare the expected structure and values with `draft.ovr_review`.
8. Reject any mismatch with a field- and position-specific error.
9. If OVR is valid, retain the existing incomplete-draft exit code `2` until human review is complete.

This behavior lets CI prove that the review numbers are authentic even while semantic completion intentionally remains red. Completed specs contain no OVR metadata and continue through the existing applicability and save-integrity checks unchanged.

The pull-request workflow continues to execute only trusted base code and materialize only one canonical `players/<slug>.json` path from the untrusted head. No permission or changed-file-boundary expansion is allowed.

## Error Handling

The OVR path fails closed.

- Ability maps must be complete; no default ability value is permitted.
- Invalid model identifiers, modes, positions, order, types, ranges, or keys are rejected.
- Unsupported positions are not silently omitted.
- Review mismatches name the position and field, for example `OVR review mismatch for RB: proposal_tenths`.
- Errors must not include fetched HTML, issue-body contents, decrypted save bytes, or other untrusted large payloads.
- Existing network trust, redirect, response-size, and Pes Retro Stats parsing behavior remains unchanged.

## Reviewer Experience

Machine JSON stores integer tenths. Human-facing CLI output renders exactly one decimal place.

The generated draft pull-request body adds two instructions:

- OVR values are community-weighted estimates, not official in-game ratings.
- Reviewers must inspect `draft.ovr_review` together with the proposed attribute and position changes.

The generator's existing two-line machine-output contract (`SPEC_PATH`, `PLAYER_NAME`) remains unchanged.

## Testing

### Shared engine

Add `tests/test_player_ovr.py` covering:

- exact known-vector results for all 13 positions;
- deterministic half-up rounding;
- canonical relevant-position selection;
- complete ability-map enforcement;
- missing and extra keys;
- bool, non-integer, and out-of-range values;
- invalid and unsupported positions;
- zero-weight defensive failure.

Each expected calculation must be independently derived, not produced by calling the implementation under test.

### Generator

Extend `tests/test_generate_player_draft.py` to cover:

- exact update comparison metadata;
- exact create proposal-only metadata;
- registered plus playable relevant positions;
- canonical ordering;
- updates that change positions but not abilities;
- generator failure when OVR inputs are invalid.

### Draft validation

Extend `tests/test_player_specs.py` and focused command tests to cover:

- exact-key schema enforcement;
- operation/mode coupling;
- duplicate, out-of-order, missing, extra, and irrelevant positions;
- value type and range enforcement;
- delta consistency;
- recomputation against the trusted base;
- rejection after tampering any one OVR value;
- valid OVR verification followed by the existing incomplete-draft exit code.

### CLI and workflow

Add focused CLI coverage proving base-only and `--spec` calculations use the shared engine. Extend `tests/test_workflow_config.py` to verify the estimate disclaimer and reviewer instruction while preserving permissions and the one-file boundary.

## Acceptance Criteria

1. OVR weights and formula exist only in `editor/player_ovr.py`.
2. Generated update drafts contain exact base, proposal, and delta tenths for relevant positions.
3. Generated create drafts contain proposal-only tenths with mode `new_player`.
4. Trusted CI rejects modified, stale, malformed, or incorrectly ordered OVR metadata.
5. Completed player specifications and save bytes contain no OVR field.
6. `tools/ovr_calc.py` renders the shared result with one decimal place.
7. Focused OVR, generator, player-spec, CLI, and workflow tests pass.
8. `python run.py players validate` exits zero for the repository's completed player specs.
9. A smoke run of the OVR CLI against the bundled base and Marco Palestra spec completes successfully.
