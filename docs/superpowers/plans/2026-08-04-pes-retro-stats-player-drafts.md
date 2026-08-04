# Pes Retro Stats Player Draft Autofill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace SortitoutSI in the Player Update contribution workflow with one-profile Pes Retro Stats autofill for reviewed `create` proposals and base-aware `update` diffs.

**Architecture:** Add a bounded Pes Retro Stats profile adapter, a pure PES 2021 mapping layer, and a base-diff layer. Keep the source, mapping, and edit-file responsibilities separate; then perform one clean schema/workflow cutover to Player Update schema version 2. Transfer-source SortitoutSI code remains untouched.

**Tech Stack:** Python 3.10+, `aiohttp`, stdlib `html.parser`/`json`/`uuid`, existing PES 2021 codec and `pesXdecrypter`, pytest, GitHub Actions YAML.

## Global Constraints

- Fetch only the single contributor-supplied public profile; never crawl, search, enumerate, or call `/api/`.
- Accept only canonical `https://pesretrostats.com/player/<8-lowercase-hex>-<slug>` URLs without credentials, port, query, or fragment.
- Explicit owner permission for programmatic profile-page access is a deployment prerequisite.
- The issue must contain a canonical `Player name`; normalized payload `name` must match it exactly. Never infer aliases.
- Generated values remain unapproved proposals until human review and merge.
- Use only the embedded PES 2021 representation. Never derive values from the displayed PES 6 block.
- `create` fails when the registered position is unsupported. `update` omits unsupported CWP/LWB/RWB registered/proficiency fields without remapping and still proposes supported fields.
- Schema version 2 is a clean cutover: no `sortitoutsi_id` alias in Player Updates.
- Keep the independent `scraper/sortitoutsi.py` transfer adapter and transfer audit ID unchanged.
- Rename workflow files to `.github/workflows/generate-player-update.yml` and `.github/workflows/validate-player-update-pr.yml`.
- Migrate Dastan's canonical file/name to `players/dastan-satpaev.json` / `Dastan Satpaev` / `SATPAEV`; do not change approved gameplay attributes.
- Add no runtime dependency.
- Permanent tests are deterministic and offline. Live profile fetches are smoke checks only.

## File Structure

### Create

- `scraper/pes_retro_stats.py` — URL validation, bounded HTTP retrieval, Next.js flight payload extraction, source-profile validation.
- `scraper/pes21_proposal.py` — pure source-to-codec mappings and deterministic age/enum conversions.
- `tools/player_draft_diff.py` — exact base-player resolution and source-target update diff construction.
- `tests/test_pes_retro_stats.py` — URL, parser, redirect, response-boundary tests.
- `tests/test_pes21_proposal.py` — complete mapping and conversion tests.
- `tests/test_player_draft_diff.py` — base match and update-diff tests.

### Modify

- `scraper/__init__.py` — export the new source adapter interfaces.
- `editor/player_codec.py` — expose decoded direct/skill/COM fields needed for update comparisons.
- `editor/player_spec.py` — schema v2 identity, UUID/profile pairing, partial-draft validation, verified-base helper.
- `editor/logger.py` — preserve transfer SortitoutSI IDs and add Pes Retro Stats UUID to Player Update audit records.
- `tools/generate_player_draft.py` — parse the new issue contract and orchestrate fetch, mapping, create proposal, and update base diff.
- `run.py` — Player Update audit UUID and user-facing generate-draft copy.
- `.github/ISSUE_TEMPLATE/player-spec.yml` — required canonical name and Pes Retro Stats profile fields.
- `.github/workflows/generate-player-update.yml` — generator workflow after rename; compile decrypter before generation.
- `.github/workflows/validate-player-update-pr.yml` — trusted Player Update validation workflow after rename.
- `players/marco-palestra.json` — schema v2 Pes Retro Stats provenance.
- `players/dastan-satpaev.json` — renamed schema v2 canonical Dastan record.
- `README.md` — contribution workflow and source semantics.
- `tests/test_generate_player_draft.py`, `tests/test_player_specs.py`, `tests/test_player_codec.py`, `tests/test_logger.py`, `tests/test_run_pipeline.py`, `tests/test_player_spec_integration.py`, `tests/test_workflow_config.py`, `tests/test_player_spec_target_workflow.py` — cutover and integration coverage.

### Remove/Rename

- Remove `scraper/player_draft.py` after all imports use `scraper/pes_retro_stats.py`.
- Replace `tests/test_player_draft.py` with `tests/test_pes_retro_stats.py`.
- Rename `.github/workflows/generate-player-spec.yml` to `.github/workflows/generate-player-update.yml`.
- Rename `.github/workflows/player-spec-pr.yml` to `.github/workflows/validate-player-update-pr.yml`.
- Rename `players/dastan-satpayev.json` to `players/dastan-satpaev.json`.

---

### Task 1: Add the bounded Pes Retro Stats profile adapter

**Files:**
- Create: `scraper/pes_retro_stats.py`
- Create: `tests/test_pes_retro_stats.py`
- Modify: `scraper/__init__.py`
- Keep temporarily: `scraper/player_draft.py`

**Interfaces:**
- Produces:
  - `class PesRetroStatsError(ValueError)`
  - `@dataclass(frozen=True, slots=True) class PesRetroStatsProfile`
  - `parse_pes_retro_stats_url(url: str) -> tuple[str, str]` returning `(short_id, canonical_url)`
  - `parse_pes_retro_stats_profile(html: str, canonical_url: str, short_id: str) -> PesRetroStatsProfile`
  - `fetch_pes_retro_stats_profile(url: str) -> Awaitable[PesRetroStatsProfile]`
- `PesRetroStatsProfile` fields:

```python
@dataclass(frozen=True, slots=True)
class PesRetroStatsProfile:
    player_id: str
    short_id: str
    name: str
    full_name: str | None
    profile_url: str
    birth_date: date
    nationality: str
    current_club: str
    shirt_number: int | None
    height: int
    weight: int
    strong_foot: str
    weak_foot_accuracy: int
    weak_foot_frequency: int
    form: int
    injury_tolerance: str
    playing_style: str | None
    positions: Mapping[str, str | None]
    stats: Mapping[str, int]
    player_skill_codes: tuple[str, ...]
    com_playing_styles: tuple[str, ...]
```

- [ ] **Step 1: Write failing canonical URL tests**

Add explicit success and rejection cases:

```python
@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://pesretrostats.com/player/0ce2dbde-marco-palestra",
            (
                "0ce2dbde",
                "https://pesretrostats.com/player/0ce2dbde-marco-palestra",
            ),
        ),
        (
            "https://pesretrostats.com/player/f77d9c27-dastan-satpaev",
            (
                "f77d9c27",
                "https://pesretrostats.com/player/f77d9c27-dastan-satpaev",
            ),
        ),
    ],
)
def test_parse_pes_retro_stats_url_accepts_canonical_profiles(url, expected):
    assert parse_pes_retro_stats_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "http://pesretrostats.com/player/0ce2dbde-marco-palestra",
        "https://www.pesretrostats.com/player/0ce2dbde-marco-palestra",
        "https://user@pesretrostats.com/player/0ce2dbde-marco-palestra",
        "https://pesretrostats.com:443/player/0ce2dbde-marco-palestra",
        "https://pesretrostats.com/player/0CE2DBDE-marco-palestra",
        "https://pesretrostats.com/player/0ce2dbde-Marco-Palestra",
        "https://pesretrostats.com/player/0ce2dbde-marco-palestra?source=test",
        "https://pesretrostats.com/player/0ce2dbde-marco-palestra#stats",
        "https://pesretrostats.com/api/player/0ce2dbde",
    ],
)
def test_parse_pes_retro_stats_url_rejects_noncanonical_inputs(url):
    with pytest.raises(PesRetroStatsError, match="Invalid Pes Retro Stats profile URL"):
        parse_pes_retro_stats_url(url)
```

- [ ] **Step 2: Run URL tests and confirm the missing-module failure**

Run: `pytest tests/test_pes_retro_stats.py -v`

Expected: collection fails because `scraper.pes_retro_stats` does not exist.

- [ ] **Step 3: Implement URL validation and the immutable profile type**

Use a strict path regex and canonical lower-case host:

```python
_ALLOWED_HOST = "pesretrostats.com"
_PROFILE_PATH_RE = re.compile(
    r"^/player/(?P<short_id>[0-9a-f]{8})-(?P<slug>[a-z0-9]+(?:-[a-z0-9]+)*)$"
)
_INVALID_URL = "Invalid Pes Retro Stats profile URL"
_UNAVAILABLE = "Pes Retro Stats profile is unavailable"
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_REDIRECT_HOPS = 5
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


def parse_pes_retro_stats_url(url: str) -> tuple[str, str]:
    if not isinstance(url, str) or not url or url != url.strip():
        raise PesRetroStatsError(_INVALID_URL)
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError):
        raise PesRetroStatsError(_INVALID_URL) from None
    match = _PROFILE_PATH_RE.fullmatch(parsed.path)
    if (
        parsed.scheme != "https"
        or parsed.hostname != _ALLOWED_HOST
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
        or match is None
    ):
        raise PesRetroStatsError(_INVALID_URL)
    canonical = urlunsplit(("https", _ALLOWED_HOST, parsed.path, "", ""))
    return match.group("short_id"), canonical
```

- [ ] **Step 4: Write failing Next.js flight payload tests**

Build a minimal server-rendered fixture by JSON-encoding `[1, flight_row]` pushes. Include all profile fields and all 25 PES 2021 source stat keys. Assert:

```python
def flight_html(*records: dict[str, object], canonical: str = PROFILE_URL) -> str:
    scripts = "".join(
        "<script>self.__next_f.push(" + json.dumps([1, "21:" + json.dumps(record)]) + ")</script>"
        for record in records
    )
    return (
        '<html><head><link rel="canonical" href="'
        + canonical
        + '"></head><body>'
        + scripts
        + "</body></html>"
    )


def test_parser_deduplicates_identical_complete_records():
    record = valid_profile_record()
    profile = parse_pes_retro_stats_profile(
        flight_html(record, dict(record)), PROFILE_URL, "0ce2dbde"
    )
    assert profile.player_id == "0ce2dbde-9cd9-423c-a90a-35b07df6a967"
    assert profile.name == "Marco Palestra"
    assert profile.player_skill_codes == ("S01", "S07")


def test_parser_rejects_two_distinct_complete_records():
    first = valid_profile_record()
    second = {**first, "id": "0ce2dbde-1111-4111-8111-111111111111"}
    with pytest.raises(PesRetroStatsError, match="unavailable"):
        parse_pes_retro_stats_profile(
            flight_html(first, second), PROFILE_URL, "0ce2dbde"
        )
```

Also add explicit tests for malformed flight JSON, incomplete record, UUID/prefix mismatch, canonical-link mismatch, non-UUID ID, wrong scalar/list/map types, duplicate skill/style entries, login/challenge HTML, and a complete record plus an incomplete `$23`/`$24` reference record.

- [ ] **Step 5: Implement flight extraction and strict record normalization**

Collect `<link rel="canonical">` and script text with `HTMLParser`. Decode only literal `self.__next_f.push([1, "..."])` calls with `json.loads`; scan decoded rows with `json.JSONDecoder.raw_decode`; retain objects containing the required profile discriminator keys. Reject reference strings such as `"$23"` where a list or object is required. Canonicalize complete candidates to immutable tuples, deduplicate equal candidates, and require one distinct result.

Validate the UUID with stdlib `UUID` and exact canonical lowercase formatting:

```python
parsed_uuid = UUID(raw_id)
if str(parsed_uuid) != raw_id or raw_id[:8] != short_id:
    raise _unavailable()
```

Store mappings as `MappingProxyType` and lists as tuples so later layers cannot mutate source evidence.

- [ ] **Step 6: Write failing bounded fetch tests**

Port the existing fake response/session helpers from `tests/test_player_draft.py`. Cover same-host redirects, redirect loops, cross-host redirects, changed short IDs, missing `Location`, non-200 status, wrong content type, invalid/oversized `Content-Length`, streamed overflow, invalid charset, timeout, and `aiohttp.ClientError`.

- [ ] **Step 7: Implement bounded fetch**

Reuse the existing safe retrieval structure with `allow_redirects=False`, 30-second total timeout, 64 KiB chunks, exact response URL revalidation, and a dedicated Pes Retro Stats user agent. Never log or return body text on error.

- [ ] **Step 8: Run adapter tests**

Run: `pytest tests/test_pes_retro_stats.py -v`

Expected: all tests pass offline.

- [ ] **Step 9: Export the new adapter without removing the old adapter yet**

Update `scraper/__init__.py` to export `PesRetroStatsError`, `PesRetroStatsProfile`, `fetch_pes_retro_stats_profile`, `parse_pes_retro_stats_profile`, and `parse_pes_retro_stats_url`. Keeping the old module until the atomic cutover avoids breaking the current generator between commits.

- [ ] **Step 10: Commit**

```bash
git add scraper/pes_retro_stats.py scraper/__init__.py tests/test_pes_retro_stats.py
git commit -m "feat(players): parse Pes Retro Stats profiles"
```

---

### Task 2: Map Pes Retro Stats values to codec proposals

**Files:**
- Create: `scraper/pes21_proposal.py`
- Create: `tests/test_pes21_proposal.py`

**Interfaces:**
- Consumes: `PesRetroStatsProfile` from Task 1 and codec constants from `editor.player_codec`.
- Produces:

```python
@dataclass(frozen=True, slots=True)
class Pes21Proposal:
    age: int
    height: int
    weight: int
    registered_position: str | None
    unsupported_positions: tuple[str, ...]
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
```

- `map_pes21_proposal(profile: PesRetroStatsProfile, *, effective_date: date) -> Pes21Proposal`

- [ ] **Step 1: Write failing complete ability-map tests**

Parameterize every pair from the approved spec and assert the output has exactly `ABILITY_FIELDS`:

```python
ABILITY_SOURCE_MAP = {
    "attacking_prowess": "attacking_awareness",
    "technique": "ball_control",
    "dribbling": "dribbling",
    "dribble_accuracy": "tight_possession",
    "short_pass_accuracy": "low_pass",
    "long_pass_accuracy": "lofted_pass",
    "shot_accuracy": "finishing",
    "heading": "heading",
    "free_kick_accuracy": "place_kicking",
    "swerve": "curl",
    "top_speed": "speed",
    "acceleration": "acceleration",
    "shot_power": "kicking_power",
    "jump": "jump",
    "physical_contact": "physical_contact",
    "body_control": "balance",
    "stamina": "stamina",
    "defensive_awareness": "defensive_awareness",
    "ball_winning": "ball_winning",
    "new_aggression": "aggression",
    "gk_awareness": "gk_awareness",
    "gk_catching": "catching",
    "gk_clearing": "clearing",
    "gk_reflexes": "reflexes",
    "gk_reach": "gk_reach",
}


def test_map_pes21_proposal_maps_every_ability(profile_factory):
    source = {name: 40 + index for index, name in enumerate(ABILITY_SOURCE_MAP)}
    proposal = map_pes21_proposal(
        profile_factory(stats=source), effective_date=date(2026, 8, 4)
    )
    assert proposal.abilities == {
        target: source[source_name]
        for source_name, target in ABILITY_SOURCE_MAP.items()
    }
    assert tuple(proposal.abilities) == ABILITY_FIELDS
```

Add failures for a missing key, extra PES 2021 key, booleans, non-integers, 39, and 100.

- [ ] **Step 2: Write failing enum, position, scale, and age tests**

Use these exact mappings:

```python
PLAYING_STYLE_IDS = {
    None: 0,
    "Goal Poacher": 1,
    "Dummy Runner": 2,
    "Fox in the Box": 3,
    "Target Man": 4,
    "Creative Playmaker": 5,
    "Prolific Winger": 6,
    "Roaming Flank": 7,
    "Cross Specialist": 8,
    "Classic No. 10": 9,
    "Hole Player": 10,
    "Box-to-Box": 11,
    "The Destroyer": 12,
    "Orchestrator": 13,
    "Anchor Man": 14,
    "Offensive Full-back": 15,
    "Full-back Finisher": 16,
    "Defensive Full-back": 17,
    "Build Up": 18,
    "Extra Frontman": 19,
    "Offensive Goalkeeper": 20,
    "Defensive Goalkeeper": 21,
}
POSITION_GRADE = {None: 0, "B": 1, "A": 2, "★": 2}
STRONG_FOOT = {"R": 0, "L": 1}
INJURY_RESISTANCE = {"C": 0, "B": 1, "A": 2}
```

Assert:

```python
@pytest.mark.parametrize(
    ("source", "encoded"),
    [(1, 0), (2, 0), (3, 1), (4, 1), (5, 2), (6, 2), (7, 3), (8, 3)],
)
def test_weak_foot_eight_point_scale_maps_to_two_bits(
    profile_factory, source, encoded
):
    proposal = map_pes21_proposal(
        profile_factory(
            weak_foot_accuracy=source,
            weak_foot_frequency=source,
        ),
        effective_date=date(2026, 8, 4),
    )
    assert proposal.weak_foot_accuracy == encoded
    assert proposal.weak_foot_usage == encoded


@pytest.mark.parametrize("source", range(1, 9))
def test_form_maps_from_one_to_eight_into_zero_to_seven(profile_factory, source):
    proposal = map_pes21_proposal(
        profile_factory(form=source), effective_date=date(2026, 8, 4)
    )
    assert proposal.form == source - 1
```

Add leap-day age tests; rejected future birth dates; supported `★` registered positions; `A`/`B` proficiency; a Dastan-style supported `SS` registration; and a Marco-style `RWB: ★` result with `registered_position is None`, `unsupported_positions == ("RWB",)`, and supported `RB`/`RMF` grades retained.

- [ ] **Step 3: Write failing complete skill and COM style tests**

Use all 41 source codes in codec order:

```python
PES_RETRO_SKILLS = dict(
    zip(
        (f"S{number:02d}" for number in range(1, 42)),
        (
            "scissors_feint", "double_touch", "flip_flap", "marseille_turn",
            "sombrero", "crossover_turn", "cut_behind_and_turn", "scotch_move",
            "step_on_skill_control", "heading_skill", "long_range_drive",
            "chip_shot_control", "long_range_shooting", "knuckle_shot",
            "dipping_shot", "rising_shot", "acrobatic_finishing", "heel_trick",
            "first_time_shot", "one_touch_pass", "through_passing",
            "weighted_pass", "pinpoint_crossing", "outside_curler", "rabona",
            "no_look_pass", "low_lofted_pass", "low_punt_trajectory",
            "gk_high_punt", "long_throw", "gk_long_throw", "penalty_specialist",
            "gk_penalty_saver", "gamesmanship", "man_marking", "track_back",
            "interception", "acrobatic_clear", "captaincy", "super_sub",
            "fighting_spirit",
        ),
        strict=True,
    )
)
PES_RETRO_COM_STYLES = {
    "Trickster": "trickster",
    "Mazing Run": "mazing_run",
    "Speeding Bullet": "speeding_bullet",
    "Incisive Run": "incisive_run",
    "Long Ball Expert": "long_ball_expert",
    "Early Cross": "early_cross",
    "Long Ranger": "long_ranger",
}
```

Assert all outputs are members of `PLAYER_SKILL_FIELDS` / `COM_STYLE_FIELDS`, preserve source order, and reject unknown or duplicate inputs.

- [ ] **Step 4: Run proposal tests and confirm failure**

Run: `pytest tests/test_pes21_proposal.py -v`

Expected: collection fails because `scraper.pes21_proposal` does not exist.

- [ ] **Step 5: Implement pure mappings with no defaults or clamping**

Use `MappingProxyType` for ability/position outputs. Require all source ability keys. Convert weak foot with `(value - 1) // 2`, form with `value - 1`, and age from `birth_date` at `effective_date`:

```python
def age_on(birth_date: date, effective_date: date) -> int:
    if effective_date < birth_date:
        raise PesRetroStatsError("Pes Retro Stats birth date is invalid")
    return effective_date.year - birth_date.year - (
        (effective_date.month, effective_date.day)
        < (birth_date.month, birth_date.day)
    )
```

A supported `★` sets `registered_position`; an unsupported `★` is recorded in `unsupported_positions` and never remapped. More than one `★` is invalid.

- [ ] **Step 6: Run proposal tests**

Run: `pytest tests/test_pes21_proposal.py -v`

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add scraper/pes21_proposal.py tests/test_pes21_proposal.py
git commit -m "feat(players): map Pes Retro Stats values"
```

---

### Task 3: Decode current fields and build exact update diffs

**Files:**
- Modify: `editor/player_codec.py`
- Create: `tools/player_draft_diff.py`
- Modify: `tests/test_player_codec.py`
- Create: `tests/test_player_draft_diff.py`

**Interfaces:**
- Extends `PlayerAbilityProfile` with `strong_foot`, `weak_foot_usage`, `weak_foot_accuracy`, `form`, `injury_resistance`, `player_skills`, and `com_styles`.
- Produces:

```python
class PlayerDraftDiffError(ValueError):
    """Raised when a base player or source-target diff is unsafe."""


@dataclass(frozen=True, slots=True)
class BasePlayerMatch:
    pes_id: int
    name: str
    print_name: str
    profile: PlayerAbilityProfile
```

- `resolve_update_player(edit_file: EditFile, *, canonical_name: str, current_team: str, source: PesRetroStatsProfile, proposal: Pes21Proposal) -> BasePlayerMatch`
- `build_update_pes(current: PlayerAbilityProfile, target: Pes21Proposal) -> dict[str, object]`

- [ ] **Step 1: Write failing codec decode assertions**

Extend the existing known-entry test:

```python
def test_decode_player_entry_exposes_every_draft_comparison_field():
    profile = decode_player_entry(PALESTRA_ENTRY)
    assert profile.strong_foot in {0, 1}
    assert 0 <= profile.weak_foot_usage <= 3
    assert 0 <= profile.weak_foot_accuracy <= 3
    assert 0 <= profile.form <= 7
    assert 0 <= profile.injury_resistance <= 3
    assert set(profile.player_skills) <= set(PLAYER_SKILL_FIELDS)
    assert set(profile.com_styles) <= set(COM_STYLE_FIELDS)
```

Add a synthetic patched entry that sets one skill and one COM style and assert their exact tuple names.

- [ ] **Step 2: Run codec test and confirm missing attributes**

Run: `pytest tests/test_player_codec.py -v`

Expected: FAIL with missing `PlayerAbilityProfile` attributes.

- [ ] **Step 3: Extend `decode_player_entry`**

Populate direct fields from `values` and decode set bits in canonical codec order:

```python
player_skills=tuple(
    name for name in PLAYER_SKILL_FIELDS if values[f"skill_{name}"]
),
com_styles=tuple(
    name for name in COM_STYLE_FIELDS if values[f"com_style_{name}"]
),
```

Do not change serialization or existing field widths.

- [ ] **Step 4: Run codec tests**

Run: `pytest tests/test_player_codec.py -v`

Expected: all pass.

- [ ] **Step 5: Write failing exact base-match tests**

Use a fake `EditFile` exposing `get_all_players`, `get_all_team_info`, `get_team_roster`, and `get_player_ability_profile`. Cover:

```python
def test_resolve_update_player_requires_one_exact_name_on_submitted_team(
    fake_edit, source_profile, proposal
):
    match = resolve_update_player(
        fake_edit,
        canonical_name="Marco Palestra",
        current_team="Chelsea FC",
        source=source_profile,
        proposal=proposal,
    )
    assert match.pes_id == 162196
    assert match.print_name == "Marco Palestra"
```

Add failures for submitted name not matching `source.name`, absent team, duplicate normalized team names, player absent from that roster, two same-name roster candidates, missing ability profile, and current-team/source-club mismatch. National-team membership must not make a unique club match ambiguous.

- [ ] **Step 6: Write failing update-diff tests**

Construct a current profile and target proposal where only speed, style, weak foot, one skill, and one COM style differ. Assert exact nested output:

```python
assert build_update_pes(current, target) == {
    "abilities": {"speed": {"from": 77, "to": 90}},
    "playing_style": {"from": 15, "to": 10},
    "weak_foot_accuracy": {"from": 1, "to": 2},
    "player_skills": {"double_touch": {"from": 0, "to": 1}},
    "com_styles": {"incisive_run": {"from": 0, "to": 1}},
}
```

Add assertions that equal fields disappear, unsupported `registered_position is None` causes no registered-position patch, unsupported RWB never appears, supported position grades still diff, removed skills/styles emit `1 -> 0`, and a fully equal proposal raises `PlayerDraftDiffError("Pes Retro Stats profile has no changes against the base")`.

- [ ] **Step 7: Run diff tests and confirm missing module failure**

Run: `pytest tests/test_player_draft_diff.py -v`

Expected: collection fails because `tools.player_draft_diff` does not exist.

- [ ] **Step 8: Implement exact matching and grouped diffs**

Use `normalize_player_identity` for equality only, never fuzzy matching. Resolve exactly one club team by normalized submitted name, require source club agreement, then require exactly one normalized player-name match in that roster. Compare every supported source-derived direct field, all abilities, all supported position proficiencies, all 41 skill booleans, and all seven COM booleans. Unflatten only changed values into the existing Player Update groups.

- [ ] **Step 9: Run codec and diff tests**

Run: `pytest tests/test_player_codec.py tests/test_player_draft_diff.py -v`

Expected: all pass.

- [ ] **Step 10: Commit**

```bash
git add editor/player_codec.py tools/player_draft_diff.py tests/test_player_codec.py tests/test_player_draft_diff.py
git commit -m "feat(players): build base-aware stat diffs"
```

---

### Task 4: Cut Player Updates over to schema version 2

**Files:**
- Modify: `editor/player_spec.py`
- Modify: `editor/logger.py`
- Modify: `run.py`
- Modify: `tests/test_player_specs.py`
- Modify: `tests/test_logger.py`
- Modify: `tests/test_run_pipeline.py`
- Modify: `tests/test_player_spec_integration.py`
- Modify: `players/marco-palestra.json`
- Rename/Modify: `players/dastan-satpayev.json` → `players/dastan-satpaev.json`

**Interfaces:**
- `PlayerIdentity.pes_retro_stats_id: str` replaces `sortitoutsi_id: int`.
- `verify_base_file(edit_path: str | Path, manifest_path: str | Path | None = None) -> BaseManifest` streams SHA-256 and raises `PlayerSpecError` on mismatch.
- Add the keyword parameter `pes_retro_stats_player_id: str | None = None` to `editor.logger.log_transfer`; retain `sortitoutsi_player_id` for transfer records.

- [ ] **Step 1: Change schema fixtures first and confirm they fail**

Update valid test payloads to:

```python
"schema_version": 2,
"identity": {
    "name": "Marco Palestra",
    "aliases": ["Marco Palestra"],
    "pes_id": 162196,
    "pes_retro_stats_id": "0ce2dbde-9cd9-423c-a90a-35b07df6a967",
},
"evidence": {
    "profile_url": "https://pesretrostats.com/player/0ce2dbde-marco-palestra",
    "proof_urls": ["https://pesretrostats.com/player/0ce2dbde-marco-palestra"],
    "effective_date": "2026-07-25",
    "reason": "Pes Retro Stats profile reviewed for attribute proposal",
},
```

Add failures for schema version 1, `sortitoutsi_id`, malformed/uppercase UUID, URL prefix mismatch, alternate host, and noncanonical profile URL.

Run: `pytest tests/test_player_specs.py -v`

Expected: failures because only schema version 1 and integer SortitoutSI IDs are accepted.

- [ ] **Step 2: Implement completed schema v2 identity and profile pairing**

Use:

```python
_PES_RETRO_STATS_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)
_PES_RETRO_STATS_PROFILE_RE = re.compile(
    r"https://pesretrostats\.com/player/(?P<prefix>[0-9a-f]{8})-"
    r"[a-z0-9]+(?:-[a-z0-9]+)*\Z"
)
```

Require `profile_match.group("prefix") == pes_retro_stats_id[:8]`. Set the only accepted schema version to 2. Remove Player Update SortitoutSI ID constants and duplicate-ID checks; add duplicate Pes Retro Stats UUID checks. Do not change `scraper.models.Transfer.player_id_sortitoutsi`.

- [ ] **Step 3: Add verified base-file tests and implementation**

Test the bundled digest success, a temporary mismatched file, and malformed manifest. Stream the file in 1 MiB chunks so the 10.5 MiB save is not copied into memory.

- [ ] **Step 4: Migrate both checked-in Player Updates**

For Marco:

```json
"schema_version": 2,
"pes_retro_stats_id": "0ce2dbde-9cd9-423c-a90a-35b07df6a967",
"profile_url": "https://pesretrostats.com/player/0ce2dbde-marco-palestra",
"proof_urls": [
  "https://pesretrostats.com/player/0ce2dbde-marco-palestra"
]
```

Remove every SortitoutSI URL and ID. Preserve all approved `pes` patches.

For Dastan, rename the file and change only identity/name provenance fields:

```json
"schema_version": 2,
"identity": {
  "name": "Dastan Satpaev",
  "print_name": "SATPAEV",
  "aliases": ["Dastan Satpaev"],
  "pes_id": 200000,
  "pes_retro_stats_id": "f77d9c27-8f02-4dbe-b877-4c13724a4886"
},
"evidence": {
  "profile_url": "https://pesretrostats.com/player/f77d9c27-dastan-satpaev"
}
```

Change `pes.name` to `Dastan Satpaev` and `pes.print_name` to `SATPAEV`. Remove the SortitoutSI proof URL but retain the QJL and Chelsea official proof URLs. Preserve all numeric gameplay and appearance values.

Update every checked-in Dastan path/name assertion in `tests/test_player_specs.py`, `tests/test_logger.py`, `tests/test_run_pipeline.py`, and `tests/test_player_spec_integration.py` to `dastan-satpaev.json`, `Dastan Satpaev`, and `SATPAEV`. The integration test must assert that a created PES record and roster entry use the new canonical name.

- [ ] **Step 5: Update audit tests before implementation**

Keep the existing transfer assertion for integer `sortitoutsi_player_id`. Add a Player Update assertion for:

```python
assert record["pes_retro_stats_player_id"] == (
    "0ce2dbde-9cd9-423c-a90a-35b07df6a967"
)
```

Update `run._player_spec_audit_record` tests so Player Updates emit the new UUID and no SortitoutSI Player Update ID.

- [ ] **Step 6: Implement the additive audit field**

Add `pes_retro_stats_player_id` to `log_transfer` and serialized records. Keep `sortitoutsi_player_id` unchanged for transfer callers. Change `_player_spec_audit_record` to use `spec.identity.pes_retro_stats_id`.

- [ ] **Step 7: Run schema, logger, and run tests**

Run: `pytest tests/test_player_specs.py tests/test_logger.py tests/test_run_pipeline.py tests/test_player_spec_integration.py -v`

Expected: all pass.

- [ ] **Step 8: Validate migrated Player Updates**

Run: `python run.py players validate`

Expected: Marco Palestra and Dastan Satpaev validate against `fl26-u2.2-national-squads`.

- [ ] **Step 9: Commit the breaking schema migration with context**

```bash
git add editor/player_spec.py editor/logger.py run.py tests/test_player_specs.py tests/test_logger.py tests/test_run_pipeline.py tests/test_player_spec_integration.py players/marco-palestra.json players/dastan-satpaev.json
git add -u -- players/dastan-satpayev.json
git commit -m "feat(players)!: adopt Pes Retro Stats identity" -m "BREAKING CHANGE: Player Updates now require schema version 2 and pes_retro_stats_id; schema version 1 sortitoutsi_id files are rejected."
```

---

### Task 5: Cut the issue generator and workflow over atomically

**Files:**
- Modify: `tools/generate_player_draft.py`
- Modify: `editor/player_spec.py` draft-validation branch
- Modify: `run.py`
- Modify: `.github/ISSUE_TEMPLATE/player-spec.yml`
- Rename/Modify: `.github/workflows/generate-player-spec.yml` → `.github/workflows/generate-player-update.yml`
- Rename/Modify: `.github/workflows/player-spec-pr.yml` → `.github/workflows/validate-player-update-pr.yml`
- Modify: `scraper/__init__.py`
- Remove: `scraper/player_draft.py`
- Remove: `tests/test_player_draft.py`
- Modify: `tests/test_generate_player_draft.py`
- Modify: `tests/test_workflow_config.py`
- Modify: `tests/test_player_spec_target_workflow.py`

**Interfaces:**
- `PlayerDraftRequest` adds `player_name: str`.
- `build_player_draft(request, source, proposal, *, edit_file: EditFile | None = None) -> dict[str, object]` emits create or update draft JSON.
- `write_player_draft(event_path: Path, output_dir: Path, *, base_edit_path: Path | None = None) -> Path` fetches one profile and decrypts only for update.

- [ ] **Step 1: Update the issue-contract tests first**

Use this exact heading order and confirmation contract:

```python
_HEADINGS = (
    "Operation",
    "Player name",
    "Pes Retro Stats profile",
    "Current team",
    "Effective date",
    "Proof URLs",
    "Contributor notes",
    "Confirmations",
)
_CONFIRMATIONS = (
    "- [X] I supplied one canonical Pes Retro Stats player profile.",
    "- [X] I understand autofilled PES values are unapproved proposals.",
    "- [X] I understand a maintainer must review the draft PR.",
)
```

Update fixture bodies with `### Player name\n\nDastan Satpaev` and the canonical profile URL. Add failures for missing/multiple names, names over 60 UTF-8 bytes, control characters, submitted/source normalized-name mismatch, wrong profile host, and noncanonical URL.

- [ ] **Step 2: Update create draft expectations**

The create draft must contain schema version 2, exact default alias, UUID, canonical profile, source metadata, and partial `pes` values. Use this exact missing list:

```python
CREATE_MISSING = (
    "identity.pes_id",
    "identity.print_name",
    "pes.player_id",
    "pes.print_name",
    "pes.team_id",
    "pes.team_name",
    "pes.nationality_id",
    "pes.skin_color",
    "pes.iris_color",
)
```

Assert `pes` contains source-derived `name`, age, height, weight, supported positions, playing style, foot/form/injury values, all abilities, skills, COM styles, and an in-range source shirt number when present. `draft.needs_human_review` remains true.

- [ ] **Step 3: Update update draft expectations**

Inject a fake `EditFile` and assert the resolved PES ID/print name, exact `from`/`to` diff, `draft.missing == []`, and absence of unsupported RWB/registered-position patches for Marco. Add absent, ambiguous, no-op, and base-verification failures.

- [ ] **Step 4: Run generator tests and confirm the cutover failures**

Run: `pytest tests/test_generate_player_draft.py tests/test_workflow_config.py tests/test_player_spec_target_workflow.py -v`

Expected: failures reference old headings, old source functions, and old workflow paths.

- [ ] **Step 5: Implement the new issue parser and source identity check**

Parse required `Player name` before the profile URL. Validate with the existing identity text rules and require:

```python
if normalize_player_identity(request.player_name) != normalize_player_identity(source.name):
    raise PlayerDraftError("Pes Retro Stats profile name does not match Player name")
```

Set `identity.name` to the submitted canonical spelling, `identity.aliases` to `[request.player_name]`, and never add `full_name` automatically.

- [ ] **Step 6: Implement create and update payload builders**

For create, reject `proposal.registered_position is None` with a clear unsupported-position error and emit the partial proposal plus `CREATE_MISSING`.

For update, require an injected/loaded `EditFile`, call `resolve_update_player`, and use `build_update_pes`. Convert `PlayerDraftDiffError` to `PlayerDraftError` without changing its safe message. Set `identity.pes_id` and `identity.print_name` from the match; set `draft.missing` to an empty list because review, not missing data, keeps it a draft.

Update `_generated_draft_missing_fields` to validate schema version 2, required UUID/profile pairing, canonical source metadata, the exact operation-specific partial `pes` shape, `CREATE_MISSING` for create, and `[]` for update. Completed-spec loading must still raise `IncompletePlayerSpecError` whenever `draft` exists.

- [ ] **Step 7: Implement verified update-base loading with cleanup**

In `write_player_draft`, use `base_edit_path or config.EDIT_FILE_PATH`. For update only:

```python
verify_base_file(base_path)
decrypted = crypto.decrypt(base_path)
try:
    edit_file = EditFile()
    edit_file.load(decrypted / "data.dat")
    payload = build_player_draft(request, source, proposal, edit_file=edit_file)
finally:
    crypto.cleanup_temp(decrypted)
```

For create, do not require the decrypter. Preserve exclusive atomic output and do not create a file on any failure.

- [ ] **Step 8: Update the issue form**

Insert required fields:

```yaml
  - type: input
    id: player_name
    attributes:
      label: Player name
      description: Enter the canonical player name exactly as shown on the Pes Retro Stats profile.
      placeholder: Dastan Satpaev
    validations:
      required: true

  - type: input
    id: pes_retro_stats_profile
    attributes:
      label: Pes Retro Stats profile
      description: Provide one canonical Pes Retro Stats player profile URL.
      placeholder: https://pesretrostats.com/player/f77d9c27-dastan-satpaev
    validations:
      required: true
```

Replace confirmation copy with the exact strings from Step 1.

- [ ] **Step 9: Rename both workflow files and update tests**

Use filesystem renames, then update only path constants and relevant generator steps:

```bash
git mv .github/workflows/generate-player-spec.yml .github/workflows/generate-player-update.yml
git mv .github/workflows/player-spec-pr.yml .github/workflows/validate-player-update-pr.yml
```

Add a generator workflow step before `Generate Player Update`:

```yaml
      - name: Compile pesXdecrypter binaries
        if: steps.existing.outputs.pr_url == ''
        shell: bash
        run: |
          set -euo pipefail
          make -C vendor/pesXdecrypter clean
          make -C vendor/pesXdecrypter
          chmod +x vendor/pesXdecrypter/decrypter21 vendor/pesXdecrypter/encrypter21
```

Keep event trust, permissions, branch isolation, machine output, and one-file PR boundary unchanged. Update `WORKFLOW_PATH`/`PLAYER_TARGET_PATH` constants in both workflow test files and assert the old workflow paths no longer exist.

- [ ] **Step 10: Remove the old Player Update source adapter**

Change generator imports and `scraper/__init__.py` exports to the new modules. Delete `scraper/player_draft.py` and `tests/test_player_draft.py`. Do not alter `scraper/sortitoutsi.py`, `fetch_sortitoutsi_transfers`, transfer reconciliation, or transfer model fields.

- [ ] **Step 11: Update CLI/help copy**

Change source-only/SortitoutSI wording in `cmd_players_generate_draft` and parser help to “reviewable Pes Retro Stats proposal.” Keep command names and two-line machine output stable.

- [ ] **Step 12: Run generator, schema-draft, and workflow tests**

Run:

```bash
pytest tests/test_generate_player_draft.py tests/test_pes_retro_stats.py tests/test_pes21_proposal.py tests/test_player_draft_diff.py tests/test_player_specs.py tests/test_workflow_config.py tests/test_player_spec_target_workflow.py -v
```

Expected: all pass; no old workflow-path assertion remains.

- [ ] **Step 13: Commit the atomic workflow cutover**

```bash
git add tools/generate_player_draft.py editor/player_spec.py run.py scraper/__init__.py .github/ISSUE_TEMPLATE/player-spec.yml .github/workflows/generate-player-update.yml .github/workflows/validate-player-update-pr.yml tests/test_generate_player_draft.py tests/test_workflow_config.py tests/test_player_spec_target_workflow.py
git add -u -- scraper/player_draft.py tests/test_player_draft.py .github/workflows/generate-player-spec.yml .github/workflows/player-spec-pr.yml
git commit -m "feat(players): autofill reviewable player drafts"
```

---

### Task 6: Document and verify the end-to-end workflow

**Files:**
- Modify: `README.md`
- Test: all focused files above, then the complete suite once.

**Interfaces:**
- No new interface. This task proves the contributor-visible workflow and live-source contract.

- [ ] **Step 1: Update README contribution and source copy**

Document the required Player name and Pes Retro Stats profile, generated source-derived values, create-only local missing fields, update base diffs, unsupported update-position omission, and unchanged human-merge approval boundary. Replace the obsolete “Approved SortitoutSI ability submissions” sentence with Pes Retro Stats proposal wording. Retain README SortitoutSI references that describe the independent transfer source.

- [ ] **Step 2: Run deterministic Player Update validation**

Run: `python run.py players validate`

Expected: both schema v2 Player Updates validate; output names Dastan Satpaev.

- [ ] **Step 3: Smoke-fetch both approved live profiles through the real adapter**

Run:

```bash
python - <<'PY'
import asyncio
from datetime import date
from scraper.pes21_proposal import map_pes21_proposal
from scraper.pes_retro_stats import fetch_pes_retro_stats_profile

cases = (
    (
        "https://pesretrostats.com/player/0ce2dbde-marco-palestra",
        "0ce2dbde-9cd9-423c-a90a-35b07df6a967",
        "Marco Palestra",
    ),
    (
        "https://pesretrostats.com/player/f77d9c27-dastan-satpaev",
        "f77d9c27-8f02-4dbe-b877-4c13724a4886",
        "Dastan Satpaev",
    ),
)

async def main():
    for url, expected_id, expected_name in cases:
        profile = await fetch_pes_retro_stats_profile(url)
        assert profile.player_id == expected_id
        assert profile.name == expected_name
        proposal = map_pes21_proposal(profile, effective_date=date(2026, 8, 4))
        if expected_name == "Marco Palestra":
            assert proposal.registered_position is None
            assert proposal.unsupported_positions == ("RWB",)
        else:
            assert proposal.registered_position == "SS"
        print(expected_name, expected_id, "ok")

asyncio.run(main())
PY
```

Expected: two `ok` lines; no API or browser access.

- [ ] **Step 4: Smoke-run the actual CLI for one create and one update**

Create two temporary GitHub issue-event fixtures with the exact form headings and confirmations, using Dastan/Create/Chelsea FC and Marco/Update/Chelsea FC. Use distinct canonical GitHub issue URLs and each profile URL as a proof URL. Then run:

```bash
python run.py players generate-draft --event "$TMPDIR/dastan-event.json" --output-dir "$TMPDIR/create"
python run.py players generate-draft --event "$TMPDIR/marco-event.json" --output-dir "$TMPDIR/update"
```

Assert the create JSON has schema version 2, canonical `Dastan Satpaev`, non-null source abilities, and exact `CREATE_MISSING`. Assert the update JSON resolves PES ID `162196`, contains at least one `from`/`to` change, and contains neither `registered_position` nor `RWB`. Remove the temporary directory afterward.

- [ ] **Step 5: Run focused regression suites**

Run:

```bash
pytest tests/test_pes_retro_stats.py tests/test_pes21_proposal.py tests/test_player_draft_diff.py tests/test_generate_player_draft.py tests/test_player_specs.py tests/test_player_codec.py tests/test_logger.py tests/test_run_pipeline.py tests/test_workflow_config.py tests/test_player_spec_target_workflow.py tests/test_player_spec_integration.py -v
```

Expected: all pass.

- [ ] **Step 6: Run the complete suite once**

Run: `pytest -v`

Expected: all tests pass on the current Python runtime.

- [ ] **Step 7: Commit documentation**

```bash
git add README.md
git commit -m "docs(players): explain Pes Retro Stats drafts"
```

- [ ] **Step 8: If verification exposed a real defect, fix at source and re-run only the failed smoke/check before repeating the complete suite once**

Do not weaken validation or skip the failing scenario. After the reproduction passes, commit a focused fix with message `fix(players): correct Pes Retro Stats draft`.
