# Community Player Spec Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let contributors request or directly submit one reviewed player spec while keeping generated Football Manager metadata separate from maintainer-approved PES values.

**Architecture:** A trusted issue-labeled workflow parses one structured GitHub issue, fetches SortitoutSI identity/provenance, writes one intentionally incomplete JSON draft, and opens a bot-owned draft PR. Existing CI validates all complete specs; a focused changed-file guard enforces one player file per contribution PR. No untrusted issue text becomes executable shell input.

**Tech Stack:** Python 3.10+, aiohttp, standard-library JSON/pathlib/urllib parsing, pytest, GitHub issue forms, GitHub Actions, GitHub CLI.

## Global Constraints

- This plan depends on `docs/superpowers/plans/2026-08-04-player-spec-engine.md` Tasks 1-6.
- Generator trigger is `issues:labeled` with exact label `generate-player-draft`.
- Workflow code always comes from the default branch, never from an issue or pull-request branch.
- The generator accepts only HTTPS SortitoutSI person URLs with numeric IDs.
- One issue produces one bot-owned draft PR and one `players/<slug>.json` file.
- Generated Football Manager data is provenance/draft metadata only; PES attributes remain explicit human inputs.
- Direct advanced-contributor PRs modify exactly one file under `players/` and use the same validator.
- Workflow permissions are `contents: write`, `pull-requests: write`, and `issues: write`; no broader permission is allowed.
- Every shell variable is derived from trusted event metadata or a path emitted through `$GITHUB_OUTPUT`.

---

### Task 1: SortitoutSI Draft Profile Fetcher

**Files:**
- Create: `scraper/player_draft.py`
- Create: `tests/test_player_draft.py`
- Modify: `scraper/__init__.py`

**Interfaces:**
- Produces: `PlayerDraftSource(sortitoutsi_id: int, name: str, profile_url: str, date_of_birth: str | None, nationality: str | None, positions: tuple[str, ...], current_club: str | None)`.
- Produces: `parse_sortitoutsi_person_url(url: str) -> tuple[int, str]`.
- Produces: `parse_sortitoutsi_player_profile(html: str, canonical_url: str, sortitoutsi_id: int) -> PlayerDraftSource`.
- Produces: `async fetch_sortitoutsi_player_profile(url: str) -> PlayerDraftSource`.

- [ ] **Step 1: Write failing URL and HTML parser tests**

Create fixed HTML fixtures inline; no live network calls:

```python
def test_parse_person_url_requires_https_sortitoutsi_numeric_id():
    from scraper.player_draft import DraftSourceError, parse_sortitoutsi_person_url

    assert parse_sortitoutsi_person_url(
        "https://sortitoutsi.net/football-manager-data-update/person/2000370206"
    ) == (2000370206, "https://sortitoutsi.net/football-manager-data-update/person/2000370206")

    for bad in (
        "http://sortitoutsi.net/football-manager-data-update/person/2000370206",
        "https://evil.example/person/2000370206",
        "https://sortitoutsi.net/football-manager-data-update/person/not-a-number",
    ):
        with pytest.raises(DraftSourceError):
            parse_sortitoutsi_person_url(bad)


def test_parse_profile_extracts_only_source_metadata():
    source = parse_sortitoutsi_player_profile(
        DASTAN_PROFILE_HTML,
        "https://sortitoutsi.net/football-manager-data-update/person/2000370206",
        2000370206,
    )
    assert source.name == "Dastan Satpayev"
    assert source.date_of_birth == "2008-08-12"
    assert source.current_club == "Chelsea"
    assert source.positions == ("AM RL", "ST")
    assert not hasattr(source, "abilities")
```

Include a Cloudflare/challenge fixture and assert a deterministic `DraftSourceError("SortitoutSI profile is unavailable")`.

- [ ] **Step 2: Run tests and confirm missing module failure**

Run: `.venv/bin/python -m pytest tests/test_player_draft.py -q`

Expected: collection fails with `ModuleNotFoundError: No module named 'scraper.player_draft'`.

- [ ] **Step 3: Implement strict URL parsing**

Use `urllib.parse.urlsplit`; require scheme `https`, hostname exactly `sortitoutsi.net` or `www.sortitoutsi.net`, and path matching `/football-manager-data-update/person/<digits>` with an optional canonical slug suffix only when SortitoutSI emits it. Drop query and fragment from the canonical URL.

- [ ] **Step 4: Implement deterministic profile parsing and fetch**

Parse embedded structured data first, then labeled HTML rows. Normalize whitespace but retain source text. Reject missing name, mismatched person ID, login/challenge pages, non-HTML response, redirects off the host allowlist, and bodies above 2 MiB. Use existing project headers and a 30-second `aiohttp.ClientTimeout`.

The returned dataclass must contain no CA, PA, ability, PES, or inferred-rating fields.

- [ ] **Step 5: Run parser tests**

Run: `.venv/bin/python -m pytest tests/test_player_draft.py -q`

Expected: URL allowlist, metadata extraction, and challenge detection pass.

- [ ] **Step 6: Commit Task 1**

```bash
git add scraper/player_draft.py scraper/__init__.py tests/test_player_draft.py
git commit -m "feat(players): fetch draft source metadata"
```

---

### Task 2: Trusted Issue Event Parser and Draft Generator

**Files:**
- Create: `tools/generate_player_draft.py`
- Create: `tests/test_generate_player_draft.py`
- Modify: `run.py`

**Interfaces:**
- Consumes: `PlayerDraftSource`, `fetch_sortitoutsi_player_profile`, and Task 1 engine `player_slug`.
- Produces: `PlayerDraftRequest(operation: str, profile_url: str, current_team: str, effective_date: str, proof_urls: tuple[str, ...], issue_number: int, issue_url: str)`.
- Produces: `parse_player_issue_event(event: Mapping[str, object]) -> PlayerDraftRequest`.
- Produces: `build_player_draft(request: PlayerDraftRequest, source: PlayerDraftSource) -> dict[str, object]`.
- Produces: `write_player_draft(event_path: Path, output_dir: Path) -> Path`.
- Produces CLI: `python run.py players generate-draft --event PATH --output-dir PATH`.

- [ ] **Step 1: Write failing event parser and draft tests**

Use a complete issue-event fixture with these headings: `Operation`, `SortitoutSI profile`, `Current team`, `Effective date`, `Proof URLs`, and `Contributor notes`.

```python
def dastan_issue_event():
    body = """### Operation

create

### SortitoutSI profile

https://sortitoutsi.net/football-manager-data-update/person/2000370206

### Current team

Chelsea FC

### Effective date

2026-08-04

### Proof URLs

https://sortitoutsi.net/football-manager-data-update/submission/fixture-proof

### Contributor notes

Missing from the reviewed FL26 base.
"""
    return {
        "action": "labeled",
        "label": {"name": "generate-player-draft"},
        "issue": {
            "number": 42,
            "state": "open",
            "html_url": "https://github.com/gvoze32/fldailyedit/issues/42",
            "user": {"type": "User"},
            "body": body,
        },
    }


def dastan_source():
    return PlayerDraftSource(
        sortitoutsi_id=2000370206,
        name="Dastan Satpayev",
        profile_url="https://sortitoutsi.net/football-manager-data-update/person/2000370206",
        date_of_birth="2008-08-12",
        nationality="Kazakhstan",
        positions=("AM RL", "ST"),
        current_club="Chelsea",
    )


def test_issue_event_builds_incomplete_non_executable_draft(monkeypatch, tmp_path):
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(dastan_issue_event()))

    async def fake_fetch(url):
        assert url.endswith("/2000370206")
        return dastan_source()

    monkeypatch.setattr(
        "tools.generate_player_draft.fetch_sortitoutsi_player_profile",
        fake_fetch,
    )

    path = write_player_draft(event_path, tmp_path / "players")
    payload = json.loads(path.read_text())

    assert path.name == "dastan-satpayev.json"
    assert payload["operation"] == "create"
    assert payload["identity"]["sortitoutsi_id"] == 2000370206
    assert payload["identity"]["pes_id"] is None
    assert payload["pes"] is None
    assert payload["draft"]["needs_human_review"] is True
```

Add rejection tests for wrong action, missing exact label, closed issue, bot author, malformed headings, multiple profile URLs, more than ten proof URLs, non-HTTPS proof URLs, and an existing `players/<slug>.json` collision.

- [ ] **Step 2: Run generator tests and confirm missing module failure**

Run: `.venv/bin/python -m pytest tests/test_generate_player_draft.py -q`

Expected: collection fails because `tools.generate_player_draft` does not exist.

- [ ] **Step 3: Implement exact issue-form body parsing**

Parse only level-three headings emitted by the checked-in form. Trim values, reject duplicate headings, and reject values above these limits: operation 10 chars, profile URL 300, current team 100, effective date 10, proof URLs 10 entries of 300, notes 2,000. Validate ISO date with `date.fromisoformat` and require `create` or `update`.

Use only `event["issue"]["number"]` for branch/identifier generation. Preserve contributor notes only inside JSON strings; never emit them to a shell fragment.

- [ ] **Step 4: Build a deliberately incomplete but valid JSON draft**

The draft contains schema version, operation, active lifecycle, current base revision, source identity/metadata, request evidence, and:

```json
"identity": {
  "name": "Dastan Satpayev",
  "print_name": null,
  "aliases": ["Dastan Satpayev"],
  "pes_id": null,
  "sortitoutsi_id": 2000370206
},
"pes": null,
"draft": {
  "needs_human_review": true,
  "missing": ["identity.pes_id", "identity.print_name", "pes"]
}
```

For `update`, the missing list is `identity.pes_id` and `pes.abilities.<field>.from/to`; do not guess target fields. Write via a temporary file and `Path.replace`, with sorted keys and trailing newline. Exit nonzero if the destination already exists.

- [ ] **Step 5: Wire the nested CLI and machine-readable output**

`generate-draft` prints two machine-readable lines; for the Dastan fixture they are `SPEC_PATH=players/dastan-satpayev.json` and `PLAYER_NAME="Dastan Satpayev"`. It accepts no network URL argument outside the trusted event file.

- [ ] **Step 6: Run generator and parser tests**

Run: `.venv/bin/python -m pytest tests/test_generate_player_draft.py tests/test_player_draft.py -q`

Expected: deterministic filename/content and every rejection path pass.

- [ ] **Step 7: Commit Task 2**

```bash
git add tools/generate_player_draft.py tests/test_generate_player_draft.py run.py
git commit -m "feat(players): generate issue draft specs"
```

---

### Task 3: Issue Form and Maintainer-Labeled Draft PR Workflow

**Files:**
- Create: `.github/ISSUE_TEMPLATE/player-spec.yml`
- Create: `.github/workflows/generate-player-spec.yml`
- Create: `tests/test_workflow_config.py`

**Interfaces:**
- Consumes: Task 2 `players generate-draft` CLI.
- Produces: structured community request form and one draft PR per labeled issue.

- [ ] **Step 1: Write failing static workflow tests**

Parse YAML as text using focused assertions so no PyYAML dependency is added:

```python
def test_generate_workflow_is_label_gated_and_minimally_privileged():
    text = Path(".github/workflows/generate-player-spec.yml").read_text()
    assert "types: [labeled]" in text
    assert "github.event.label.name == 'generate-player-draft'" in text
    assert "contents: write" in text
    assert "pull-requests: write" in text
    assert "issues: write" in text
    assert "GITHUB_EVENT_PATH" in text
    assert "pull_request_target" not in text
    assert "issue.body" not in text


def test_issue_form_headings_match_event_parser_contract():
    text = Path(".github/ISSUE_TEMPLATE/player-spec.yml").read_text()
    for field_id in ("operation", "sortitoutsi_profile", "current_team", "effective_date", "proof_urls", "contributor_notes"):
        assert f"id: {field_id}" in text
```

- [ ] **Step 2: Run static tests and verify missing-file failures**

Run: `.venv/bin/python -m pytest tests/test_workflow_config.py -q`

Expected: `FileNotFoundError` for the issue form/workflow.

- [ ] **Step 3: Create the issue form**

The form explains that the request creates a draft, not an approved player. Make operation, SortitoutSI profile, current team, effective date, and proof URLs required. Include checkboxes confirming the contributor supplied source evidence, did not derive PES ratings from FM values, and understands a maintainer must review the PR.

Set default labels to `player-spec` only. The privileged `generate-player-draft` label is intentionally absent and must be applied later by a maintainer.

- [ ] **Step 4: Create the trusted labeled-issue workflow**

Use this structure:

```yaml
on:
  issues:
    types: [labeled]

permissions:
  contents: write
  pull-requests: write
  issues: write

jobs:
  generate:
    if: github.event.label.name == 'generate-player-draft'
    concurrency: player-draft-${{ github.event.issue.number }}
```

Checkout the default branch with persisted credentials, set up Python 3.13, install the project, invoke the generator using `$GITHUB_EVENT_PATH`, read `SPEC_PATH` from a generated `$GITHUB_OUTPUT` file, and create branch `player-draft/issue-${{ github.event.issue.number }}`.

Use `gh pr list --head` to make reruns idempotent. Commit only the emitted `players/*.json` path. Create a draft PR with title `player: draft <name>` and body linking the issue. Comment the PR URL back to the issue. Quote every shell expansion.

- [ ] **Step 5: Add actionlint-compatible shell safeguards**

Set `shell: bash`, `set -euo pipefail`, no `eval`, no interpolated issue body, no actor-controlled branch name, and no write of event strings into `$GITHUB_ENV`. Use a Python step to append percent/newline-safe values to `$GITHUB_OUTPUT`.

- [ ] **Step 6: Run workflow tests**

Run: `.venv/bin/python -m pytest tests/test_workflow_config.py tests/test_generate_player_draft.py -q`

Expected: trigger, permissions, parser/form contract, and unsafe-expression checks pass.

- [ ] **Step 7: Commit Task 3**

```bash
git add .github/ISSUE_TEMPLATE/player-spec.yml .github/workflows/generate-player-spec.yml tests/test_workflow_config.py
git commit -m "ci(players): open labeled draft PRs"
```

---

### Task 4: Player Contribution Guard and Shared Validation

**Files:**
- Create: `tools/check_player_spec_pr.py`
- Create: `tests/test_player_spec_pr.py`
- Modify: `.github/workflows/ci.yml`
- Modify: `run.py`

**Interfaces:**
- Consumes: engine-plan `players validate` command and `load_player_specs`.
- Produces: `validate_player_pr_changes(changes: Sequence[str]) -> Path | None`.
- Produces CLI: `python tools/check_player_spec_pr.py --changes-file PATH`.

- [ ] **Step 1: Write failing contribution-boundary tests**

```python
@pytest.mark.parametrize(
    "changes",
    [
        ["A\tplayers/a.json", "A\tplayers/b.json"],
        ["A\tplayers/a.json", "M\trun.py"],
        ["A\tplayers/a.json", "M\t.github/workflows/ci.yml"],
        ["A\tplayers/a.json", "M\tdata/base_manifest.json"],
        ["A\tplayers/A.json"],
        ["D\tplayers/marco-palestra.json"],
        ["R100\tplayers/old.json\tplayers/new.json"],
    ],
)
def test_player_contribution_rejects_unsafe_change_sets(changes):
    with pytest.raises(PlayerContributionError):
        validate_player_pr_changes(changes)


def test_player_contribution_accepts_one_canonical_player_file():
    assert validate_player_pr_changes(["A\tplayers/marco-palestra.json"]) == Path(
        "players/marco-palestra.json"
    )


def test_non_player_pull_request_needs_no_player_guard():
    assert validate_player_pr_changes(["M\tREADME.md"]) is None
```

Reject non-JSON player files. A pull request with no `players/` change returns `None`; once a player file changes, that add/modify record must be the only change.

- [ ] **Step 2: Run guard tests and verify missing module failure**

Run: `.venv/bin/python -m pytest tests/test_player_spec_pr.py -q`

Expected: collection fails because the guard module is missing.

- [ ] **Step 3: Implement the changed-path guard**

Read tab-delimited status records created by `git diff --name-status --diff-filter=ACDMRTUXB \"${GITHUB_BASE_SHA}...HEAD\"`, where the workflow sets `GITHUB_BASE_SHA` from `github.event.pull_request.base.sha`. Normalize each path with `PurePosixPath`; reject absolute paths, `..`, control characters, uppercase filenames, deletion/rename/copy statuses, and every change outside the one canonical `players/<slug>.json` file.

The script must not run git, fetch remotes, or evaluate shell text itself.

- [ ] **Step 4: Add CI validation jobs**

Keep the existing Python matrix. Add one unconditional step on all CI jobs:

```bash
python run.py players validate
```

Run the `player-spec-pr` job for every pull request. The guard exits successfully when no `players/` path changed; when one did, it requires that path to be the pull request's only changed file. In the job, fetch the PR base commit, write changed paths to a temporary file, run the guard, then run the shared player validator.

The generated draft PR is expected to fail semantic validation until a human fills all required values. State that explicitly in the draft PR body.

- [ ] **Step 5: Test CI configuration and guard**

Run: `.venv/bin/python -m pytest tests/test_player_spec_pr.py tests/test_workflow_config.py tests/test_player_specs.py -q`

Expected: changed-file rules and shared validation pass.

- [ ] **Step 6: Commit Task 4**

```bash
git add tools/check_player_spec_pr.py tests/test_player_spec_pr.py .github/workflows/ci.yml run.py
git commit -m "ci(players): validate single-spec PRs"
```

---

### Task 5: Community Workflow Documentation and End-to-End Verification

**Files:**
- Modify: `README.md`
- Modify: `MEMORY.md`
- Modify only if verification finds a defect: files from Tasks 1-4.

**Interfaces:**
- Documents and verifies the full issue-to-draft-PR path.

- [ ] **Step 1: Document both contribution paths**

README must include:

1. simple contributor opens the player-spec issue, adds evidence, and waits for maintainer label;
2. workflow opens an intentionally incomplete draft PR;
3. contributor/maintainer fills explicit PES values and expected baselines;
4. CI validates the one-file PR;
5. merge approval is approval state;
6. advanced contributor may directly open a one-player PR;
7. official base update leaves historical specs in place but makes unmatched revisions inactive.

MEMORY must list the generator, issue form, workflows, label, threat model, and exact local validation commands.

- [ ] **Step 2: Run the full test suite**

Run: `.venv/bin/python -m pytest -q`

Expected: all tests pass.

- [ ] **Step 3: Exercise generator with a recorded issue event**

Run the CLI against the Dastan event fixture in a temporary directory while stubbing HTTP through the test harness. Verify one deterministic JSON file is written, issue text appears only as JSON strings, and rerun fails without overwriting the file.

- [ ] **Step 4: Validate both contribution outcomes**

Run `python run.py players validate` against:

- the generated incomplete draft: expected nonzero with an exact list of missing human fields;
- the checked-in reviewed Dastan/Marco specs: expected success.

- [ ] **Step 5: Validate workflow syntax**

If `actionlint` is installed, run `actionlint .github/workflows/generate-player-spec.yml .github/workflows/ci.yml`. Otherwise parse both files with Ruby's bundled YAML parser using safe loading and rely on `tests/test_workflow_config.py` for GitHub-specific assertions. Expected: both YAML files parse and static security assertions pass.

- [ ] **Step 6: Review permissions and injection boundaries**

Manually verify from the checked-in workflow text:

- default-branch checkout only;
- exact label gate;
- numeric issue branch name;
- no direct `${{ github.event.issue.body }}` in shell;
- no `pull_request_target`;
- only emitted canonical spec path is committed;
- exact three write permissions.

- [ ] **Step 7: Commit documentation**

```bash
git add README.md MEMORY.md
git commit -m "docs: explain player spec contributions"
```
