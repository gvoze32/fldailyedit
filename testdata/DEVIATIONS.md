# Intentional deviations from the original Python implementation

One section per area. Each entry: what changed, why, and which golden output it affects.

## Planning (fl-engine `planning`)

- **Skipped rows carry `retryable` (audit B6).** `SkippedTransfer` has a new trailing `retryable: bool`
  (`true` only for `not_yet_effective`, `source_snapshot_missing`, `snapshot_incomplete`,
  `snapshot_coverage_low`, `destination_roster_full`). The since-window derivation pins the scrape window only on
  retryable rows instead of every relevant row. Affects `skipped_transfers.jsonl` rows (extra key) and the window
  after the first run with permanent skips; the golden scenarios start from an empty state, so their plans are unchanged.
- **Ambiguous / outvoted shirt observations are reported (audit B14).** `dedupe_shirt_number_matches` used to count
  dropped shirt observations silently. Whole groups dropped as ambiguous become skipped rows with reason
  `shirt_number_ambiguous`; observations that lost to a stronger one with a *different* number become
  `shirt_number_outvoted`. Same-number duplicates stay silent (harmless). Affects the skipped report when a scrape
  contains conflicting shirt numbers for one player and club.
- **End of loan clears the parent's loan marker (audit B9).** The Python loan ledger only cleared the *borrower*
  on `end of loan`; the parent kept a stale marker that could later move a returned player from wherever he sat.
  Affects only runs where a loan return is followed by an unrelated event naming the parent.
- **Fast-mode switches removed.** `allow_uncovered_source` (fast mode) is gone; planning always behaves as the
  Python `--deep` run (`allow_uncovered_source=False`). No change to the deep goldens.
- **Scraped events are never mutated (audit B10).** The inferred-source rewrite applies to the matched copy only.
  No output change.
- **One canonical event order.** Python had two sort keys that only agreed because shirt observations are never
  dated; the single key is (shirt updates last, undated after dated, chronological, stable). No output change.
- **Overflow candidate is no longer removed from the simulated roster before the move is validated (audit B13).**
  The validated-first order cannot differ today (the guarded branch is unreachable).
- **Events already in the transfer log are never applied again (reruns).** `plan_roster_actions` takes
  `already_applied` (`build_already_applied`): an event whose player, source, destination (a release has none) and
  event date are in this save's log (dated events only) is `noop` whatever the player's current club is, and so is
  every earlier event of the same player. Unit tests: `rerun_does_not_replay_logged_chain`,
  `rerun_treats_events_before_a_logged_one_as_obsolete`, `rerun_does_not_readd_a_released_logged_signing`
  (`planning/tests/matching.rs`). The rest of the rerun work is in "Idempotency (reruns)" below.

## Idempotency (reruns)

Users run the update repeatedly on their own save. Probe: replay a scenario, then run again in place on the output
(same cassette, pinned clock, state dir kept). The Python oracle is not idempotent: on `small` its second pass logs 14
transfers + 1 shirt number on top of the first pass's 81 log rows (chains replayed, overflow churn at Estudiantes de
Río Cuarto, live-squad evidence arriving late), and on `full` the second pass fails integrity validation and rolls back
(Al-Gharafa, below). Rust before this work was a faithful copy (small: 4 extra passes needed; full: pass 2 failed).
Root causes and fixes, each with a regression test:

1. **A club lost its last goalkeeper (full pass 2 failed).** Al-Gharafa (team 67024): pass 2's live squad lists two
   keepers the save cannot match ("Khalifa Ababacar" vs the save's "Khalifa Ndiaye"), so the plan released both of the
   club's goalkeepers. Compaction then promoted CF 171371 into role 0 while the position arrays kept goalkeeper code 0
   for it: `Team 67024 game-plan preset 0x4 assigns CF player 171371 goalkeeper position code 0`. No repair can be
   valid with no goalkeeper on the roster, so `release_player` and `move_player` now refuse to remove a club's last
   goalkeeper (`RosterErrorKind::LastGoalkeeper`, code `last_goalkeeper`, a safe refusal: the event is skipped and
   reported). Nobody else being a goalkeeper *or of unknown position* counts as "last" (integrity validation does not
   judge unknown positions, and the first full pass leaves Olympiakos with such a stand-in). Tests:
   `roster/tests/goalkeeper_tests.rs` on the exact pass-1 state of the club (`testdata/roster/al_gharafa_pass1.json`:
   roster, shirts, game plan, positions, the six releases and repair options); the test fails without the guard with the
   exact nine validation errors above. The roster parity sequences stop at the first refused step
   (`moves_drain_native`).
2. **Overflow churn.** A signing the feed lists that the save already held is a `noop` (never logged), so a later
   signing's overflow release could drop him and every later run re-added one and dropped the next (Forclaz, Bersano,
   Leiva at Estudiantes). The planner now never picks as overflow casualty a player whose last matched event of the
   feed names this club (`feed_members`), as live-squad members already were. Test:
   `overflow_never_releases_a_player_the_feed_puts_at_the_club`.
3. **Evidence that feeds on the roster it describes.** A live squad's 75% coverage gate opens once the first
   registrations landed (Johor Darul Ta'zim), and a free agent's identity follows his Player.bin metadata once he is
   rostered. One pass therefore left work for the next run (small: 4 extra passes; full: at least 6). A run now plans
   and applies in rounds on the in-memory save until a round changes nothing (`MAX_ROUNDS` = 16, then a warning), and
   writes once. Each round after the first starts without session state (as a fresh run would) and reloads the player
   catalog, matcher and roster tables (`context::refresh`); its history is the log plus the previous rounds' records;
   counts add up, "already current"/"safety-skipped"/refused rows are the last round's. Progress goes back from
   Applying to Matching between rounds (the GUI never rewinds). `Applied::absorb` is unit-tested; the whole is tested by
   the rerun tests. `gameplan_changed` is now "a game plan's bytes changed" (`RepairMetrics::changed_game_plans`):
   the counters also count steps a later step undid (3 clubs forever in `full`), which made rounds endless.
4. **Namesakes traded places between runs.** The matcher read ages only from the catalog, which merges Player.bin for
   *rostered* players, so a free agent was "age unknown" (compatible with anyone) until a run registered him and then
   incompatible: in `full` two "Diego Méndez" (GK, 36) flipped each pass (add one, release the other). The matcher now
   takes ages from Player.bin for every player that has a record (`match_db`, test
   `native_age_and_position_apply_to_free_agents_too`). Positions stay the catalog's: applying native *positions* to
   every free agent too was tried and rejected exact-name matches whose feed position label differs (+298
   `player_not_matched` in the full skip report, 63 applied events lost); with ages only `player_not_matched` falls
   2734 → 2661.
5. **Coverage is judged on the saved rosters.** `build_snapshot_roster_coverage` used the roster after this run's events
   had moved players around in the matching simulation; a later run scrapes fewer events, so the gate depended on the
   window. It now reads the rosters as saved (what the Johor note above always claimed).

What a rerun can still change: a later run's scrape window starts a week before the previous run, so events that
protected a player from the live-squad release (a feed event naming the club) can be out of the window; the live squad
then wins. `small` has none (a `--club` run keeps its window): pass 2 is `NoChanges`. `full` needs one extra rerun:
the first run settles in 5 rounds (small: 3), pass 2 applies 155 live-squad registrations/releases and 100 shirt numbers
only (no dated event is replayed, asserted), pass 3 is `NoChanges` (`full_scenario_rerun_settles`). `provider_identity_metadata_conflict` rows rise in the skip report because
later rounds know identities from this run's registrations (reclassified from `player_not_matched`, not lost).

## Matcher and club identity (fl-engine `matcher`, `club_identity`, `match_db`)

Parity is proven against "Python + these fixes" (the former Python reference matcher, now removed, applied them by source patch and also
recorded the unpatched answer; `deviations` in the fixtures lists every query where they differ). Over the committed
corpus (10 000 player + 12 816 team queries, 496 + 583 sequential squad replays, name pass over the whole FotMob
catalog) the unpatched Python differs on 35 player queries, 0 team queries, 0 identity results. None affects a golden
scenario: the pipeline never supplies database nationalities (`PlayerInfo` has no nationality) and loads team aliases
only for names that match exactly.

- **Nationality is compared by whole tokens (audit B3).** Python used substring containment both ways, so
  "niger"~"nigeria", "oman"~"romania", "ireland"~"northern ireland", "guinea"~"equatorial guinea". Two nationalities
  now match when their token lists are equal or one is a run of whole tokens in the other and the extra tokens are not
  distinguishing ("north", "south", "equatorial", "bissau", "dr", "new", ...): "republic of ireland"~"ireland",
  "korea republic"~"korea", never "guinea"~"papua new guinea". A supplied nationality that folds to nothing ("  ") no
  longer matches every candidate (+6 score / tie-break winner). One implementation serves the score bonus and both tie-breakers.
- **Fuzzy club hits on an alias target report their real score (audit B7).** Python returned 100 for any fuzzy step 4/5
  hit when the query was an alias, even though the alias target itself was not found by name. Exact and
  affix-cleaned-exact alias hits still report 100. Cannot differ in the pipeline: aliases are the save's own club names.
- **Results are typed (audit B5).** `PlayerMatch`/`TeamMatch` carry a `MatchStatus` (and `id` is `Some` only for
  `Matched`). `confidence` keeps Python's number (e.g. 100 for a rejected exact name) so reports are byte-identical.
- **Club-name collisions in the match database.** Carried over: like Python, `build_match_database` keys clubs by
  name, so of two save clubs with the same name only the last (by team id) is matchable by name.
- **Club identity cache is written only by `ClubIdentityIndex::save()`** (dry runs never call it; audit B5 of the
  pipeline). Output bytes equal Python's (`indent=2`, sorted keys, other save scopes preserved).

Carried over on purpose (documented, not fixed):

- **Token-set subset = 100 for clubs (audit B4).** `token_set_ratio("inter", "inter miami") == 100`. This is also how
  the name pass binds most clubs: of the 118 fuzzy name bindings on the base save 104 are strict token-subset pairs
  ("Ajax Amsterdam"~"Ajax", "SL Benfica"~"Benfica"). A blanket fix would unbind them (they would then never be fetched
  and learned); the few that look wrong ("Union Saint Gilloise"~"Union", "Muangthong United FC"~"United Fc") are corrected by
  squad-overlap evidence, which always overrides name bindings.
- **Age gate rejects an exact name outright (audit B6)** (pinned by `test_exact_name_with_conflicting_age_is_rejected`).
- **Candidate recall is `token_set_ratio >= threshold - 12` (audit B19).**
- Player id 0 carries no metadata evidence (Python `candidate_pid and ...`); real ids are never 0.

## Pipeline (fl-engine `pipeline`, `scrape`, `log`, `report`, `backup`, `lock`, `native`)

The idempotency work changes the output of the golden scenarios (see "Baseline"); the other deviations below do not.
Behavioral differences:

- **Dry run writes nothing (audit B5).** Python persisted learned club identities before the dry-run branch. The
  Rust dry run skips `club_identity.save()`, takes no lock, writes no backup/log/report, and keeps the FotMob payload
  and tactics caches in a scratch directory that is deleted afterwards. Proven by `dry_run_writes_nothing_anywhere`.
- **Backup only right before the commit (audit B16).** Failed verification no longer leaves a backup that evicts
  real ones. Backups live in `<state>/backups/<scope-hash>/` (per save) and are pruned to 10 per save, so saves no
  longer evict each other.
- **Verification compares integrity errors structurally (audit B15).** An error is "pre-existing" when the same
  kind occurs for the same team/player, whatever its counts or sizes; Python compared message strings.
- **UTC everywhere (audit B7).** "Today", log timestamps and the previous-window rule use the UTC date from the
  injected clock (Python mixed local `date.today()` with UTC). The since-window is pinned only by retryable skipped
  rows (B6, see Planning).
- **Transfermarkt honors the window end (audit B8).** Python passed only `since`; with `--window winter/summer` and
  no explicit date the scan now stops at the window end like every other source. Affects only runs with a named window.
- **No process-wide configuration (audit B3).** The game root travels in the request; Player.bin/Team.bin/
  PlayerAssignment.bin are loaded per run from it (or an explicit file override in `Services`). A threshold of `0`
  is honored (B1). The eager 11 MB re-read of the input (B2) is gone.
- **Rollback restores a snapshot (audit B11).** Apply/verify failures restore the validated input bytes through
  `EditFile::restore`.
- **Reports are per save.** `transfer_summary.md/html` and `skipped_transfers.jsonl` are written to
  `<state>/reports/<scope-hash>/` instead of the global `output/` folder; `GITHUB_STEP_SUMMARY` support is dropped.
  `skipped_transfers.jsonl` rows gain `retryable` only when it is true (the golden rows are unchanged).
- **Best-effort sources report why they were skipped.** An unavailable supplemental provider (Wikipedia,
  Sortitoutsi, Transfermarkt, BeSoccer, Sofascore, Soccerway) becomes a run warning and an empty batch; Python logged
  and printed it. A failed FotMob fetch still aborts the run before anything is written.
- **Apply-time refusals use typed codes.** `RosterErrorKind` replaces `last_mutation_error_code`; the ten safe
  codes that have a typed counterpart (`last_goalkeeper` is new) (`overflow_release_failed` has none) skip one event (reported as a skipped
  row), every other refusal rolls the whole run back.
- **Output-file races share `InputChanged`.** A changed or appearing/vanishing output file fails with
  `ErrorCode::InputChanged` (Python had a separate `output_changed` code); the message tells which file changed.
- **`UpdateResult.skipped[].transfer_type` is empty.** Skipped rows carry no transfer type in the oracle either.
- **BeSoccer feed works again (fl-sources `besoccer.rs`).** BeSoccer now links players on `es.besoccer.com`, which
  Python's proof-link pattern never matched, so Python parsed zero BeSoccer routes. Rust accepts any subdomain. In the
  full golden five verified events gain `besoccer`, `https://www.besoccer.es/fichajes` and an
  `https://es.besoccer.com/jugador/...` proof URL in their provenance: Leon Bailey (Aston Villa→Olympiacos,
  2026-09-03), Richard Ríos (Benfica→Al-Ittihad), Rafael Navarro (Colorado Rapids→St. Louis City), Carlos Álvarez
  (Levante→CF América) and Job (Kongsvinger→FC Dallas). The e2e baseline contains the BeSoccer provenance. Other
  supplemental-source deviations (Retry-After dates, Soccerway empty-feed warning, Transfermarkt ids kept on merge):
  `testdata/sources_other/DEVIATIONS.md`.

## Baseline (e2e goldens)

`testdata/e2e/small/expected`, `testdata/local/e2e/full/expected` and the `expected`/captain/lineup-preference results
of both `plan.json.gz` were re-baselined from the reviewed Rust output (`FL_E2E_REBASELINE=1`, see
`testdata/e2e/README.md`); the tests compare the engine's full output exactly (no golden views, BeSoccer provenance
included). Causes of the differences from the Python recording, in order of effect: the settle rounds (live-squad
registrations, releases and shirt numbers the Python run left for the next run), age-aware namesake resolution for
free agents, feed-confirmed players kept out of overflow, coverage on saved rosters, and the last-goalkeeper refusal.
Game-plan bytes change wherever the roster did (lineup compaction/backfill, role bytes, bench order).

**small** (8 clubs differ; log 81 → 91 rows; skipped rows 77 → 74). Per team (game-plan bytes lineup/roles/other):

| team | roster | players | shirts | game-plan bytes lineup/roles/other | why (log rows touching the team, + new / − old) |
|---|---|---|---|---|---|
| Avispa Fukuoka (169) | 34→33 | −Shahab Zahedi (165222) | – | 21/2/0 | 1× +live-squad registration |
| CA Banfield (1927) | 30→30 | – | Matías Hernández 44→11 | – | 1× +dated event (add); 1× -dated event (move) |
| CA San Lorenzo (1243) | 36→36 | – | Emiliano Amor 2→12, Danilo Arboleda 12→2 | 8/0/0 | 2× +dated event (add); 1× +shirt number; 1× -dated event (move); 1× -dated event (release) |
| Estudiantes de Río Cuarto (1925) | 40→40 | +Ezequiel Forclaz (153006) −Nicolás Morro (182122) | Fernando 26→14 | 14/0/0 | game-plan repair / no logged change |
| FK Qarabağ (4326) | 23→21 | −Kevin Medina (54979), Dani Bolt (140199) | – | 27/1/0 | 2× +live-squad registration |
| Independiente Rivadavia (2726) | 32→33 | +Matías Reali (166637) | – | 22/0/0 | 1× +dated event (add) |
| Johor Darul Ta'zim (70484) | 40→35 | +Kevin Medina (54979), Dani Bolt (140199), Shahab Zahedi (165222) −Heberty (16555), Eddy Silvestre (100187), Samu Castillejo (102623), Romel Morales (111300), Park Jun-Heong (122665), Cristian Glauder (125878), Raúl Parra (148039), Teto Martín (151739) | Hector Hevel 87→8 | 33/0/0 | 3× +live-squad registration; 5× +live-squad release; 1× +shirt number |
| Panathinaikos FC (198) | 35→35 | – | – | 3/0/0 | game-plan repair / no logged change |

Dated events only in the baseline: San Lorenzo +2 adds and Independiente Rivadavia +1 (the namesake "Matías Reali" now resolves to
the right player and reaches Independiente Rivadavia), Banfield +1 add; only in Python: 1 move, 1 release.

**full** (287 of 496 bound clubs differ; log 6787 → 7259 rows; skipped rows 6603 → 6508): 99 players added and 255
removed on 135 rosters, 194 shirt numbers changed on 73 clubs, 3856 lineup and 98 role bytes changed on 281 game plans
(no formation/tactics/position-array bytes). Log rows only in the baseline: 135 live-squad registrations, 201
live-squad releases, 136 shirt numbers, 1 captain, 109 dated events (43 moves, 36 adds, 30 releases: events Python
skipped as `current_club_mismatch`/unmatched that the settled rosters make applicable, and namesakes resolved by age);
only in Python: 110 rows (29 registrations, 4 releases, 71 shirt numbers, 5 dated events, 1 captain: superseded by
the settled state or re-resolved namesakes). Skip-reason deltas: `current_club_mismatch` 521 → 478,
`snapshot_coverage_low` 237 → 177, `player_not_matched` 2734 → 2661, `provider_identity_metadata_conflict` 135 → 206
(reclassified from `player_not_matched`, see Idempotency), `last_goalkeeper` 0 → 1, `shirt_number_ambiguous/outvoted`
now also reported. Per-team detail for `full`: `testdata/local/e2e/full/REBASELINE.md` (local, gitignored).

## Game-folder-only names (no bundled FL catalog)

`data/FL2622wc_players.txt` and `data/FL262_teams.txt` are no longer embedded: every save reads its player and club lists from the
game's own `Player.bin` / `Team.bin` (CPKs below the game folder), and a missing or mismatched database fails the run early with
`NativeDatabaseMissing`. (`data/players.csv`, kept at that time for T99 alias names, was removed later: see "T99 support dropped".)

Evidence before the removal (reference/data_extra.cpk against the bundled lists):

* Player.bin covers every rostered id of `base/EDIT00000000` and of the five FL saves in `reference/` (18 912 – 19 389 ids each).
  Three ids of `EDIT00000000epltactic` are not in Player.bin; all three are edited inside that save, so the save's own record covers them.
* Names: 18 911 of 18 911 Player.bin-covered rostered ids identical to the bundled list on the base save (0 different; 1 rostered id
  is missing from the list); 0 different on every other FL save.
* Team.bin: all 583 club ids of every FL save are present with the identical name (583 / 583).
* `reference/Player.bin` (= `data_s2526.cpk`, 27 840 players) is **not** enough: it misses 1 534 rostered ids of the base save. The game's
  `data_extra.cpk` Player.bin (29 492 players) is the one discovery prefers, so the e2e scenarios now use it (plus its `Team.bin`,
  `inputs/Team.bin.gz`); the old inputs only worked because the bundled names covered the gap.

What changes in the engine:

* The catalog is the players edited inside the save plus, from Player.bin, metadata for every rostered player and a name-only entry for
  every other player of the game (free agents stay matchable, their position stays unknown as before). Where the save and Player.bin
  both name a player, the save's name wins (it is what the game displays); the bundled list used to win. Five players of the base save
  differ (Joseph Opoku/Okopu, Manu/Kouadio Koné, Víctor Valdepeñas/Valde, Chupe/Chupete, Jeff/Julian Chabot).
* FL club names come from Team.bin (identical to the removed list); PES 2021 / T99 keep the save's own names.
* 11 ids the bundled list named but the game's Player.bin does not contain are gone (Alexandre Pato 35266, Manuel Hidalgo 131217,
  Rinsei Ohata, Renji Hidano, Mostafa Shobeir, Kanta Sekitomi, Yusei Yamanouchi, Ginjiro Ikegaya, Ryuki Osa, Paul Mendy, Steeve Kango;
  none is edited in the base save): the old engine could register them although the game has no such player. One Player.bin id the
  list lacked now has a name. T99 saves also gain name-only entries of the non-rostered players of their Player.bin (Japanese names,
  no effect on matching).

Baseline change (both scenarios, same cassettes): the `Player.bin` swap (s2526 → data_extra) explains nearly all of it. Measured by
running the pre-change engine on the data_extra inputs: only the rows below differ between that run and the new engine.

| scenario | skipped rows | log rows | changed by the catalog removal itself |
|---|---|---|---|
| small | 74 → 72 | 91 → 92 | 1 registration (Manuel Hidalgo, 131217, a bundled-list-only id) not made; Johor Darul Ta'zim therefore releases Eddy Silvestre instead |
| full | 6 508 → 6 490 | 7 259 → 7 306 | 18 log rows only before, 3 only after: 9 registrations of bundled-list-only ids not made (Hidalgo, Ohata, Hidano, Sekitomi, Yamanouchi, Ikegaya, Osa, Mendy, Kango); the other rows are shirt/release rows of players on those clubs and of the five save-vs-Player.bin name differences (Valdepeñas/Valde, Opoku/Okopu, Koné); 1 `player_not_matched` skip fewer |

Skip reasons before → after (`full`): `player_not_matched` 2661 → 2648, `current_club_mismatch` 478 → 481, `destination_team_not_matched` 1257 → 1258,
`source_team_not_matched` 1117 → 1120, `snapshot_coverage_low` 177 → 168, `provider_identity_metadata_conflict` 206 → 204,
`already_registered_elsewhere` 35 → 39, `outside_save` 545 → 535, `shirt_number_ambiguous` 4 → 6, `shirt_number_outvoted` 1 → 2,
`last_goalkeeper` 1 → 2, new `source_player_missing` 1. `small`: `destination_team_not_matched` 26 → 25, `player_not_matched` 17 → 15,
`source_team_not_matched` 17 → 18.

## T99 support dropped

Support for the T99 patch is dropped. `data/players.csv` (English alias names that T99 saves used for their localized players) and
everything that existed only for it are removed: `fl-save::catalog::{EMBEDDED_LEGACY_PLAYERS_CSV, load_legacy_player_names,
legacy_name_matches_native, PlayerCatalogError}`, the alias block and `MatchDatabase::native_aliases` in `match_db.rs`, and the `csv`
dependency of `fl-save`. `build_match_database` is infallible now. Football Life and vanilla PES 2021 read player and club data from the
game folder's `Player.bin` / `Team.bin` and never used the aliases, so their goldens are unchanged. Local-only parity tests against
`reference/` T99 files (save tables, roster sequences, protected-TOC CPK) stay: they exercise generic save/CPK code.
