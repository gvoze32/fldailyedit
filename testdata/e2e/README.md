# End-to-end goldens

Each scenario directory is one recorded real run of the former Python pipeline (the HTTP cassette) plus the expected outputs
of the offline replay. The cassette, the base save and the scenario arguments come from the former Python implementation (removed); `expected/`
was first produced by it and is now **the Rust engine's own reviewed output**: after the idempotency work the engine
no longer reproduces the Python save byte for byte (`testdata/DEVIATIONS.md`, "Baseline" lists every difference per
team). The Rust tests compare every artifact exactly against it (`crates/fl-engine/tests/e2e_replay.rs`,
`planning_golden.rs`).

Regenerate the baseline after an intended change and review the diff:

```sh
FL_E2E_REBASELINE=1 cargo test --release -p fl-engine --test e2e_replay --test planning_golden -- --include-ignored
```

(`e2e_replay` rewrites `expected/*`, `meta.json` hashes and, for `full`, `EDIT00000000.gz`; `planning_golden` rewrites the
`expected` stages, the captain and lineup-preference results of `plan.json.gz`.) Rerun settling is tested by
`small_scenario_rerun_changes_nothing` and `full_scenario_rerun_settles`.

```
<scenario>/
  meta.json            pinned clock (today, now_utc), input save, run args, layout, cassette rules, hashes, recorded stats
  cassette.jsonl.gz    HTTP cassette, one JSON object per line (contract: see Commands below)
  expected/            EDIT00000000.delta.json.gz (sparse patch over `input`), transfer_summary.md/html,
                       skipped_transfers.jsonl, transfer_log.jsonl, club_identity_cache.json
```

Paths inside `expected/` text files are normalised: the run's work directory is written as `<WORKDIR>`
(`<WORKDIR>/input/EDIT00000000`, `<WORKDIR>/output/EDIT00000000`, `<WORKDIR>/state/...`).

The save output depends on the game's `Player.bin` and `Team.bin` (every save reads its player and club lists from the game), which are
game data and are NOT committed: they are gitignored scenario inputs at
`testdata/local/e2e/<name>/inputs/Player.bin.gz` and `Team.bin.gz` (gzip; sha256 of the raw files in `meta.json` → `player_bin.sha256`,
`team_bin.sha256`; both taken from `reference/data_extra.cpk`, the archive the game overrides `data_s2526.cpk` with;
decompress to `<WORKDIR>/inputs/Player.bin` / `Team.bin` and load them as the run's databases). Rust e2e tests must SKIP (not fail) when
either file is absent.

`small/` is committed (6 clubs via `--club`). The full coverage run (`--deep`) lives in gitignored `testdata/local/e2e/full/`.

## Commands (repo root)

Scenarios are frozen recordings; there is no way to record new ones. The cassette contract: one JSON object per line
`{method, url, status, headers, body_b64}`, keyed by method + URL; replay never touches the network.

```sh
# offline replay of the committed scenario(s)
cargo test -p fl-engine --test e2e_replay
# full run (also the gitignored scenarios in testdata/local; needs testdata/local)
cargo test -p fl-engine --test e2e_replay -- --include-ignored
# re-baseline `expected/` after an intended change, then review the diff
FL_E2E_REBASELINE=1 cargo test --release -p fl-engine --test e2e_replay --test planning_golden -- --include-ignored
```
