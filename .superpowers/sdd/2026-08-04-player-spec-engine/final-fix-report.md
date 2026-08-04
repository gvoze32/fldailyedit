# Player spec engine final fix report

Date: 2026-08-04
Base: `08f157d`
Commit: `fix(players): fail unsafe spec application states` (the commit containing this report)

## Changes

- `players validate` now exits 2 unless each assessment is `ready`/`waiting`, a matching `upstreamed`/`retired` lifecycle-history result, or an active spec's revision-level `needs_review` result.
- Unexpected per-spec mutation exceptions retain isolated rollback and independent processing, return `rejected/mutation_failed` with the exception type/message diagnostic, and make `players apply` exit 2 only after any successful independent mutations are validated, encrypted, roundtrip-verified, audited, and reported.
- Update assessment includes every required edit-category marker. All-target values with a missing marker are `ready` for a marker-restoring update rather than `already_applied`.
- Non-null create `preferred_shirt_number` values are restricted to allocator-supported `1..99`.

## Red evidence

1. Shirt-number allocator boundary:

```text
$ .venv/bin/python -m pytest -q tests/test_player_specs.py -k 'preferred_shirt_number'
FAILED tests/test_player_specs.py::test_create_preferred_shirt_number_rejects_values_outside_allocator_range[100]
Failed: DID NOT RAISE PlayerSpecError
1 failed, 3 passed, 52 deselected in 0.10s
```

2. Missing required update markers:

```text
$ .venv/bin/python -m pytest -q tests/test_player_specs.py -k 'restore_missing_required_marker'
FAILED ...[speed-77-80-edited_abilities] - expected updated, got already_applied
FAILED ...[nationality_id-215-216-edited_basic_settings] - expected updated, got already_applied
FAILED ...[skill_scissors_feint-0-1-edited_skills] - expected updated, got already_applied
3 failed, 53 deselected in 0.08s
```

3. Mutation exception diagnostic:

```text
$ .venv/bin/python -m pytest -q tests/test_player_specs.py -k 'failed_mutation_rolls_back_only_that_spec and exception'
FAILED tests/test_player_specs.py::test_failed_mutation_rolls_back_only_that_spec[exception-mutation_failed]
AttributeError: 'SpecResult' object has no attribute 'diagnostic'
1 failed, 55 deselected in 0.09s
```

4. Validation current-state gate:

```text
$ .venv/bin/python -m pytest -q tests/test_run_pipeline.py -k 'players_validate_rejects_invalid_current_active_state or players_validate_permits_applicable_and_history_states'
FAILED ...[already_applied-matching_player_exists] - Failed: DID NOT RAISE SystemExit
FAILED ...[conflict-mixed_or_unexpected_values] - Failed: DID NOT RAISE SystemExit
FAILED ...[rejected-pes_id_missing] - Failed: DID NOT RAISE SystemExit
3 failed, 5 passed, 11 deselected in 0.31s
```

5. Apply failure-only and partial-success exit status:

```text
$ .venv/bin/python -m pytest -q tests/test_run_pipeline.py -k 'failure_only_batch or mixed_success_and_mutation_failure'
FAILED tests/test_run_pipeline.py::test_players_apply_failure_only_batch_exits_nonzero_without_output - Failed: DID NOT RAISE SystemExit
FAILED tests/test_run_pipeline.py::test_players_apply_mixed_success_and_mutation_failure_persists_verified_success - Failed: DID NOT RAISE SystemExit
2 failed, 19 deselected in 0.21s
```

## Green evidence

```text
$ .venv/bin/python -m pytest -q tests/test_player_specs.py -k 'preferred_shirt_number or restore_missing_required_marker or failed_mutation_rolls_back_only_that_spec'
.........                                                                [100%]
9 passed, 47 deselected in 0.11s
```

```text
$ .venv/bin/python -m pytest -q tests/test_run_pipeline.py -k 'players_validate_rejects_invalid_current_active_state or players_validate_permits_applicable_and_history_states or failure_only_batch or mixed_success_and_mutation_failure'
..........                                                               [100%]
10 passed, 11 deselected in 0.20s
```

Focused files, including existing waiting/ready, mixed/third-value conflict, rollback, audit ordering, full-roster, and roundtrip coverage:

```text
$ .venv/bin/python -m pytest -q tests/test_player_specs.py tests/test_run_pipeline.py
........................................................................ [ 93%]
.....                                                                    [100%]
pytest: 77 passed in 0.29s
```

Full suite:

```text
$ .venv/bin/python -m pytest -q
........................................................................ [ 27%]
........................................................................ [ 54%]
........................................................................ [ 81%]
................................................                         [100%]
pytest: 264 passed in 1.38s
```

Bundled semantic validation:

```text
$ .venv/bin/python run.py players validate
  Dastan Satpayev (PES ID 200000): waiting (destination_roster_full)
  Marco Palestra (PES ID 162196): ready (all_current)
Player specs: active=2, needs-review=0, upstreamed=0, retired=0, create=1, update=1
```

## Self-review

- Confirmed failure-only batches write no backup/output; mixed batches persist, validate, encrypt, roundtrip-verify, audit, and rebuild reports before exiting 2.
- Confirmed exception rollback remains per-spec and successful independent mutations remain visible and auditable.
- Confirmed all-target idempotency requires abilities, basic-settings, skills, and the other marker categories derived by the shared marker function.
- Confirmed transfer `run` and overflow/auto-release behavior were not changed.
- Confirmed unrelated `scraper/sortitoutsi.py` worktree changes remain untouched.
- Final reviewer re-reviewed the uncommitted diff and reported `clean`.

## Concerns

None identified.
