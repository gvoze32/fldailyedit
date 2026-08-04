# Pes Retro Stats Player Draft Autofill Design

## Goal

Replace SortitoutSI only in the Player Update contribution workflow with Pes Retro Stats. A submitted Pes Retro Stats profile must prefill reviewable PES 2021 values before approval for both `create` and `update` operations. The existing SortitoutSI transfer-signal integration remains unchanged.

The generated values are proposals, not approval. A Player Update becomes effective only after a reviewer verifies the draft, completes local-only fields, removes draft metadata, and merges the pull request.

## Source permission and access boundary

The integration assumes the project has explicit permission from the Pes Retro Stats owner to fetch public player profile pages programmatically. It does not use private or undocumented API endpoints.

Each issue event must supply one canonical player name and may fetch only the single profile URL supplied by the contributor. The workflow does not crawl the sitemap, search the site, enumerate players, or use browser automation. The submitted name must match the normalized `name` in the profile payload; the generator never invents aliases.

Accepted profile URLs must:

- use HTTPS;
- use the exact host `pesretrostats.com`;
- match `/player/<8 lowercase hexadecimal characters>-<canonical slug>`;
- contain no credentials, port, query, or fragment; and
- remain on the allowlisted host through every redirect.

## Scope

### Included

- Player Update issue form and issue-event parser
- Public profile fetcher and parser
- PES 2021 field mapping
- Generated `create` proposals
- Generated `update` diffs against the configured base revision
- Player Update schema migration from version 1 to version 2
- Existing Marco Palestra and Dastan Satpaev Player Updates
- Workflow files renamed to `.github/workflows/generate-player-update.yml` and `.github/workflows/validate-player-update-pr.yml`, plus workflow copy, validator messages, exports, tests, and README Player Update documentation

### Excluded

- SortitoutSI transfer submissions and daily transfer reconciliation
- Pes Retro Stats transfer history
- Automatic approval or application to an edit file
- Site-wide crawling or player search
- PES 6 values
- Automatic changes to gameplay values already approved in existing Player Updates

## Source adapter

A dedicated Pes Retro Stats Player Update adapter replaces the SortitoutSI-specific player-profile adapter. Transfer modules keep their current names and behavior.

The adapter performs a bounded HTTP fetch with the repository's existing safety model:

- explicit user agent;
- total timeout;
- maximum response size;
- maximum redirect count;
- HTTPS and host validation before every request and after every redirect;
- accepted `text/html` response type only; and
- fail-closed handling for non-200 responses, invalid encodings, malformed HTML, and incomplete bodies.

The parser reads the server-rendered Next.js flight payload and collects structurally complete player-record candidates. It deduplicates semantically identical repeated copies, then requires exactly one distinct player record. It does not scrape rendered label text because the page contains both PES 6 and PES 21 presentations.

The following identities must agree:

1. the required canonical player name submitted in the issue;
2. the normalized `name` in the payload;
3. the eight-character ID prefix in the requested URL;
4. the canonical URL in the response;
5. the first eight characters of the full player UUID in the payload; and
6. the single player record used for the draft.

A missing record, multiple distinct candidate records, identity mismatch, unknown field type, or schema drift is an error. The adapter never emits a partially parsed source model.

The normalized source model contains:

- full Pes Retro Stats UUID;
- canonical profile URL;
- canonical source name and optional full name;
- birth date, nationality, current club, shirt number, height, and weight;
- registered and playable positions;
- PES 2021 abilities;
- playing style;
- strong foot, weak-foot settings, form, and injury tolerance;
- player skills; and
- COM playing styles.

## PES 2021 mapping

Only the PES 2021 representation in the embedded record is accepted. The adapter maps source fields to codec fields through explicit allowlists.

### Abilities

| Pes Retro Stats | Player Update codec |
|---|---|
| `attacking_prowess` | `attacking_awareness` |
| `technique` | `ball_control` |
| `dribbling` | `dribbling` |
| `dribble_accuracy` | `tight_possession` |
| `short_pass_accuracy` | `low_pass` |
| `long_pass_accuracy` | `lofted_pass` |
| `shot_accuracy` | `finishing` |
| `heading` | `heading` |
| `free_kick_accuracy` | `place_kicking` |
| `swerve` | `curl` |
| `top_speed` | `speed` |
| `acceleration` | `acceleration` |
| `shot_power` | `kicking_power` |
| `jump` | `jump` |
| `physical_contact` | `physical_contact` |
| `body_control` | `balance` |
| `stamina` | `stamina` |
| `defensive_awareness` | `defensive_awareness` |
| `ball_winning` | `ball_winning` |
| `new_aggression` | `aggression` |
| `gk_awareness` | `gk_awareness` |
| `gk_catching` | `catching` |
| `gk_clearing` | `clearing` |
| `gk_reflexes` | `reflexes` |
| `gk_reach` | `gk_reach` |

Every ability must be an integer in the codec's accepted range. The source must provide all ability fields required for a complete create proposal.

### Enums and collections

Position grades, playing-style names, strong-foot values, injury-tolerance values, skill codes, and COM-style names use explicit source-to-codec tables. Unknown values are errors rather than silently ignored values. A `create` profile must identify exactly one registered position supported by the codec. For `update`, an unsupported registered position such as CWP/LWB/RWB and its unsupported proficiency field are omitted without remapping; all other supported proposal fields remain available.

Source display scales that differ from the encoded PES 2021 scale use named, tested conversion functions. Values are range-checked after conversion. No clamping is permitted.

Player skills and COM styles are converted as sets, reject duplicates, and must contain only values supported by `PLAYER_SKILL_FIELDS` and `COM_STYLE_FIELDS`.

Age is derived deterministically from the birth date at the issue's effective date. Height and weight are copied only when present and valid.

## Player Update schema version 2

Schema version 2 removes `identity.sortitoutsi_id` from the Player Update contract and adds required `identity.pes_retro_stats_id`, a canonical lowercase UUID string.

`evidence.profile_url` must be the canonical Pes Retro Stats URL whose payload UUID matches `identity.pes_retro_stats_id`. Proof URLs remain independent HTTPS citations.

Generated-draft source metadata expands to hold the normalized Pes Retro Stats profile and its proposed PES 2021 fields. Completed Player Updates retain the existing strict `pes` create/update shapes.

There is no compatibility alias for `sortitoutsi_id`. All Player Update callers and files migrate together. SortitoutSI identifiers used by the separate transfer data model are not renamed or removed.

## Draft generation

### Create

A `create` draft pre-populates every source-derived field:

- the required submitted canonical name, after exact normalized agreement with the source; aliases default to that name only and are never inferred;
- age, height, and weight;
- registered position and position proficiency;
- playing style;
- strong foot, weak-foot usage and accuracy, form, and injury resistance;
- all PES 2021 abilities;
- player skills; and
- COM styles.

Fields that cannot be derived safely remain explicit items in `draft.missing`:

- local PES player ID;
- print name;
- local destination team ID and reviewed team name;
- local nationality ID;
- skin color;
- iris color; and
- preferred shirt number when it cannot be accepted from the source.

The partial generated representation is valid only as a draft. Completed schema validation still requires a full create record.

### Update

An `update` draft needs current base values. The generator workflow therefore compiles `pesXdecrypter`, verifies the configured base manifest, decrypts the bundled base, and resolves the submitted player against that exact revision.

Matching is fail-closed. The generator uses the required submitted canonical name, submitted current team, source identity, birth-derived age, and supported position evidence, and it must resolve exactly one current player. An ambiguous or absent match produces no draft.

For each source-derived field supported by the codec, the generator compares the decoded base value with the mapped Pes Retro Stats target. It emits only changed fields as literal `from` and `to` pairs in the existing update groups. Equal fields and unsupported registered/position fields are omitted. Zero differences is a controlled no-op and does not create an empty or misleading draft PR.

The reviewer may edit or remove any proposed difference before approval.

## Approval lifecycle

Autofill does not change the repository's approval boundary:

1. A contributor submits the canonical player name, one Pes Retro Stats profile, and supporting evidence.
2. A maintainer applies the exact generator label.
3. The workflow opens one draft PR containing one generated Player Update.
4. The draft remains intentionally invalid for completed-spec validation while `draft.needs_human_review` and `draft.missing` are present.
5. A reviewer verifies source identity and values, completes local-only fields, and removes draft-only metadata.
6. CI validates the completed schema and semantics.
7. Merge is approval. Applying Player Updates to a save remains an explicit command.

No fetched value is written directly to an edit file by the generator.

## Existing Player Update migration

The two current files migrate to schema version 2 without changing their approved gameplay attributes.

### Marco Palestra

- Profile: `https://pesretrostats.com/player/0ce2dbde-marco-palestra`
- UUID: `0ce2dbde-9cd9-423c-a90a-35b07df6a967`

### Dastan Satpaev

- Rename `players/dastan-satpayev.json` to `players/dastan-satpaev.json`.
- Change canonical identity name and serialized PES name to `Dastan Satpaev`.
- Change print name to `SATPAEV`.
- Keep only `Dastan Satpaev` as the default alias; no spelling variants are inferred.
- Profile: `https://pesretrostats.com/player/f77d9c27-dastan-satpaev`
- UUID: `f77d9c27-8f02-4dbe-b877-4c13724a4886`

All SortitoutSI IDs and URLs are removed from these Player Update files. Existing official non-SortitoutSI proof URLs remain. The migration does not alter already reviewed ability, position, style, skill, physical, or appearance values.

## Failure handling

The generator fails without writing a destination file when any of these conditions occurs:

- invalid or non-canonical URL;
- redirect outside the allowlist;
- timeout, oversized response, unsupported content type, or non-200 status;
- unavailable/challenge/login response;
- missing, duplicate, or malformed embedded player record;
- URL/canonical/payload identity mismatch;
- missing required PES 2021 fields;
- unknown enum, skill, style, position, or invalid range;
- base manifest or decryption failure for update;
- absent or ambiguous base-player match; or
- update with no observable differences.

Errors exposed to untrusted issue content remain concise and do not echo response bodies, credentials, or arbitrary payload text.

## Verification

Offline automated coverage must include:

- URL canonicalization and rejection of credentials, ports, queries, fragments, alternate hosts, HTTP, malformed IDs, and redirect escapes;
- bounded response handling and cleanup on fetch failures;
- minimal Next.js payload parsing with one valid record;
- missing, duplicated, malformed, and mismatched records;
- submitted-name agreement and rejection of automatic aliases;
- every ability mapping;
- every supported position grade, playing style, foot, injury value, skill code, and COM style, plus omission of unsupported update positions without remapping;
- scale conversions and range boundaries;
- create draft prefill and exact `draft.missing` output;
- update base matching and exact changed-field diff output;
- absent, ambiguous, no-op, and base-revision failure paths;
- schema version 2 strictness and rejection of `sortitoutsi_id` in Player Updates;
- migrated Marco and Dastan files, including the canonical Dastan filename/name change;
- issue-template configuration and both renamed workflow files;
- unchanged SortitoutSI transfer behavior.

Implementation smoke verification must also:

1. fetch and parse the two approved live profile URLs;
2. confirm their canonical URLs and full UUIDs;
3. exercise one create proposal and one update diff through the actual generator path;
4. validate all completed Player Updates; and
5. run the focused Player Update and workflow test suites.

Live network checks are smoke verification, not permanent tests, so the test suite remains deterministic and offline.
