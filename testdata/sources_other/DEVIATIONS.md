# Supplemental sources: deliberate deviations from the Python oracle

Everything else in `fl-sources` (`transfermarkt`, `wikipedia`, `soccerway`,
`sofascore`, `besoccer`, `sortitoutsi`, `reconcile`) is parser/flow parity with
the frozen Python code, verified by `crates/fl-sources/tests/other_sources_*.rs`
against `testdata/sources_other/` (regenerate with
`.venv/bin/python tools/oracle/sources_other.py record|dump`).

| # | Area | Python behaviour | Rust behaviour | Effect on e2e golden |
|---|------|------------------|----------------|----------------------|
| 1 | BeSoccer | Proof-link pattern only accepts `besoccer.com/es` or `www.` hosts; BeSoccer now links `es.besoccer.com`, so the source returned **nothing** on live pages | Any subdomain accepted | A replayed cassette with a current BeSoccer page yields corroborators (Python: none). Revert = one regex in `besoccer.rs` (`PLAYER_URL`) |
| 2 | Transfermarkt | `fetch_transfermarkt_transfers` takes no window, so the lower bound is the window start and the upper bound only "today" (audit B8) | Upper bound = window end clamped to today (`resolve_date_range`) | Only differs for `--window winter/summer` runs: TM rows after the window end are no longer emitted |
| 3 | Transfermarkt | Parse errors / bad `since` (`date.fromisoformat`) propagate and could abort the run (audit B2) | Every failure is a `SourceError`; invalid `since` is `SourceError::Date` before any request | none |
| 4 | Merge field table | `fotmob.merge_transfers` lost Transfermarkt ids when two sources shared a route key before `_merge_provenance` ran | `model::merge_transfers` (SourcesFotMob) fills the four Transfermarkt ids too; `reconcile::merge_provenance` keeps Python's own field set | Transfermarkt ids survive on merged events; the 4 large generated reconcile fixtures differ from stock Python only through this, the oracle uses a copy of the Python merge with the extended table (`_unified_merge_transfers`) |
| 5 | Wikipedia | `Retry-After` HTTP-date unsupported (audit B10) | Seconds or HTTP-date, capped at 5 s | none (retry timing only) |
| 6 | Soccerway | Zero feed rows for every resolved club was silent (audit B15) | `warn!` when every resolved club had an empty feed | none (log only) |
| 7 | Sofascore | `curl_cffi` Chrome TLS impersonation | rustls + Chrome headers; same candidate order (see `sofascore.rs`) | none when the first candidate is a replay hit; live runs reach the page via the `sofascore.app` fallback |
