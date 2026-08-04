# Player Update Terminology Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace public “player spec” jargon with the plain-language feature name “Player Update” without changing any technical contract or behavior.

**Architecture:** Treat terminology as a presentation boundary. README, Issue Form copy, workflow display text, CLI messages, and report labels use “Player Update”; Python identifiers, JSON values, file paths, labels, job IDs, parser headings, commands, and machine output remain unchanged. Existing static and behavioral tests pin both the new public wording and the unchanged compatibility-sensitive values.

**Tech Stack:** Python 3.10+, pytest, GitHub Issue Forms YAML, GitHub Actions YAML, Markdown

## Global Constraints

- Public feature name is exactly **Player Update**; plural is **Player Updates**.
- Do not introduce “Player Change,” “Player Edit,” or “Player Data Update” as alternate feature names.
- Keep Python `player_spec` identifiers, JSON schema, `create`/`update` values, CLI commands, paths, workflow filenames, job IDs, branch names, and machine-readable fields unchanged.
- Keep GitHub labels `player-spec` and `generate-player-draft` unchanged.
- Keep the Issue Form parser headings, their order, and confirmation text unchanged.
- Keep Issue Form dropdown options exactly `create` and `update`; explain their meaning in help text because GitHub submits options exactly as displayed.
- Do not change workflow triggers, conditions, permissions, checkout refs, shell boundaries, or security controls.
- Preserve the unrelated local `scraper/sortitoutsi.py` modification and deleted historical SDD report; never reset, clean, stash, or stage them.
- Design source: `docs/superpowers/specs/2026-08-04-player-update-terminology-design.md`.

---

### Task 1: README and Issue Form Public Copy

**Files:**
- Modify: `README.md:51-59,95-178`
- Modify: `.github/ISSUE_TEMPLATE/player-spec.yml:1-15`
- Modify: `tests/test_workflow_config.py:140-180,537-552`

**Interfaces:**
- Consumes: existing Issue Form parser contract with ordered headings and literal `create`/`update` values.
- Produces: public feature heading `Player Updates` and Issue Form identity `Player Update Request` while preserving every compatibility-sensitive form field.

- [ ] **Step 1: Write failing public-copy tests**

Add focused assertions to `tests/test_workflow_config.py`:

```python
def test_readme_uses_player_update_language_for_public_contributions():
    text = README_PATH.read_text(encoding="utf-8")
    assert "## Player Updates" in text
    assert "Validate all Player Updates against the pristine base" in text
    assert "Apply reviewed Player Updates explicitly to one save" in text
    assert "player update issue form" in text
    assert "CI accepts a Player Update only when" in text
    assert "## Player-spec contributions" not in text


def test_issue_form_uses_plain_player_update_copy_without_changing_contract():
    text = FORM_PATH.read_text(encoding="utf-8")
    assert "name: Player Update Request" in text
    assert 'title: "[Player Update]: "' in text
    assert (
        "description: Request a new player or an update to an existing player. "
        "A maintainer will review the data before it is added."
        in text
    )
    assert (
        "Choose create to add a new player, or update to change an existing player."
        in text
    )
    assert 'labels: ["player-spec"]' in text
    assert "        - create\n        - update" in text
    fields = _field_blocks(text)
    assert tuple(
        (field_type, _field_id(block), _field_label(block))
        for field_type, block in fields
    ) == EXPECTED_FIELDS
```

Retain the existing tests for default labels, exact heading order, required fields, confirmation labels, and parser coupling. In `test_issue_form_matches_the_generator_heading_contract_exactly`, replace the obsolete `\"not an approved player\"` copy assertion with the new exact description above. In `test_readme_lists_every_whitelisted_update_patch_group_and_pair_contract`, split on `\"## Player Updates\"` instead of the retired heading.

- [ ] **Step 2: Run the focused tests and verify red**

Run:

```bash
.venv/bin/python -m pytest tests/test_workflow_config.py -q \
  -k 'readme_uses_player_update or issue_form_uses_plain_player_update'
```

Expected: both tests fail because README and Issue Form still display “player spec” or “player specification.”

- [ ] **Step 3: Update README public terminology**

Apply these exact wording decisions:

```text
Daily prebuilt saves through GitHub Actions
Reviewed player creations and attribute corrections through explicit Player Update commands

# Validate one-file-per-player updates against the pristine base revision
# Apply reviewed Player Updates explicitly to an existing output save

players validate | Validate all Player Updates against the pristine base
players apply    | Apply reviewed Player Updates explicitly to one save

run handles transfers only: it never loads or applies Player Updates.

## Player Updates
```

In the contribution section:

- write “Each reviewed Player Update is one JSON file per player”;
- call full create payloads “new-player updates” and patch payloads “existing-player updates”;
- use link text “player update issue form” while retaining `.github/ISSUE_TEMPLATE/player-spec.yml`;
- say “Update files must also state the expected current (`from`) value”;
- say “CI accepts a Player Update only when the PR adds or modifies exactly one canonical player JSON path.”

Do not rename commands, paths, schema fields, operation values, lifecycle values, or labels shown as literal code.

- [ ] **Step 4: Update Issue Form public copy**

Set the header to:

```yaml
name: Player Update Request
description: Request a new player or an update to an existing player. A maintainer will review the data before it is added.
title: "[Player Update]: "
labels: ["player-spec"]
```

Set the operation description to:

```yaml
description: Choose create to add a new player, or update to change an existing player.
```

Keep `label: Operation`, options `create` and `update`, all remaining headings, and all confirmation labels byte-for-byte unchanged.

- [ ] **Step 5: Run form, parser, and documentation tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_workflow_config.py \
  tests/test_generate_player_draft.py -q
```

Expected: all tests pass; the form still produces the exact body consumed by the trusted issue parser.

- [ ] **Step 6: Commit Task 1**

```bash
git add README.md .github/ISSUE_TEMPLATE/player-spec.yml tests/test_workflow_config.py
git commit -m "docs(players): clarify update terminology"
```

---

### Task 2: GitHub Workflow Display Copy

**Files:**
- Modify: `.github/workflows/generate-player-spec.yml:1,132-137,253-277`
- Modify: `.github/workflows/player-spec-pr.yml:1,11-12,102,130`
- Modify: `.github/workflows/ci.yml:42`
- Modify: `tests/test_workflow_config.py`

**Interfaces:**
- Consumes: unchanged workflow paths, event labels, job IDs, output IDs, branch names, PR lookup, and security boundaries.
- Produces: GitHub Actions and PR copy that consistently calls the feature “Player Update.”

- [ ] **Step 1: Write failing workflow-copy tests**

Add this static contract to `tests/test_workflow_config.py`:

```python
def test_workflows_use_player_update_copy_on_public_surfaces():
    generator = WORKFLOW_PATH.read_text(encoding="utf-8")
    target = PLAYER_TARGET_PATH.read_text(encoding="utf-8")
    ci = CI_PATH.read_text(encoding="utf-8")

    assert generator.startswith("name: Generate Player Update Draft\n")
    assert "- name: Generate Player Update" in generator
    assert 'COMMENT_BODY="Draft Player Update: $pr_url"' in generator
    assert '--title "Draft Player Update: $PLAYER_NAME"' in generator
    assert (
        "This is an incomplete Player Update. Semantic validation must fail "
        "until a human fills every required value."
        in generator
    )

    assert target.startswith("name: Validate Player Update pull request\n")
    assert "name: Validate trusted Player Update boundary" in target
    assert "- name: Materialize validated Player Update" in target
    assert "- name: Validate materialized Player Update" in target
    assert "- name: Validate Player Updates" in ci
```

Do not assert the absence of `player-spec`: internal workflow paths, labels, job IDs, and temporary filenames intentionally retain it.

- [ ] **Step 2: Run the focused workflow-copy test and verify red**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_workflow_config.py::test_workflows_use_player_update_copy_on_public_surfaces -q
```

Expected: FAIL on the old workflow display names, comment, PR title, and body.

- [ ] **Step 3: Change display text only**

Use these replacements:

```yaml
name: Generate Player Update Draft
- name: Generate Player Update

name: Validate Player Update pull request
name: Validate trusted Player Update boundary
- name: Materialize validated Player Update
- name: Validate materialized Player Update

- name: Validate Player Updates
```

In both issue-comment paths use:

```bash
COMMENT_BODY="Draft Player Update: $pr_url"
```

Use this generated PR title and body:

```bash
--title "Draft Player Update: $PLAYER_NAME"
printf -v PR_BODY \
  'Closes #%s\n\nThis is an incomplete Player Update. Semantic validation must fail until a human fills every required value.' \
  "$ISSUE_NUMBER"
```

Do not rename workflow files, `player-spec-pr` job ID, `player-draft` branch prefix, `generate-player-draft` label, `remote-player-spec.json`, shell variables, GitHub output fields, or generated commit message. Do not change expressions or shell control flow.

- [ ] **Step 4: Run workflow security and integration tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_workflow_config.py \
  tests/test_player_spec_target_workflow.py -q
```

Expected: all tests pass, including permissions, trusted-base checkout, head-as-data materialization, PR reuse, branch recovery, exact form/parser coupling, and new copy assertions.

- [ ] **Step 5: Safe-load all modified workflows**

Run:

```bash
ruby -e 'require "yaml"; ARGV.each { |path| YAML.safe_load_file(path, permitted_classes: [], permitted_symbols: [], aliases: false); puts "#{path}: safe-load ok" }' \
  .github/workflows/generate-player-spec.yml \
  .github/workflows/player-spec-pr.yml \
  .github/workflows/ci.yml
```

Expected: all three files print `safe-load ok`.

- [ ] **Step 6: Commit Task 2**

```bash
git add .github/workflows/generate-player-spec.yml \
  .github/workflows/player-spec-pr.yml \
  .github/workflows/ci.yml tests/test_workflow_config.py
git commit -m "ci(players): clarify update workflow copy"
```

---

### Task 3: CLI and Audit Report User Messages

**Files:**
- Modify: `run.py:1681-1685,1735-1755,1860-1863,1915-1955,2065-2087`
- Modify: `editor/logger.py:302-331,408-430`
- Modify: `tests/test_generate_player_draft.py:455-462`
- Modify: `tests/test_run_pipeline.py:668-702`
- Modify: `tests/test_logger.py:80-104`

**Interfaces:**
- Consumes: unchanged CLI commands, Python functions, exception classes, audit record types, JSONL keys, and machine-readable generator output.
- Produces: plain-language terminal and report copy using “Player Update.”

- [ ] **Step 1: Write failing CLI and report copy tests**

Update or add behavioral assertions:

```python
# tests/test_generate_player_draft.py
assert capsys.readouterr().out.splitlines() == [
    "Player Update validation failed: incomplete draft dastan-satpayev.json",
    "Missing human fields: identity.pes_id, identity.print_name, pes",
]

# tests/test_run_pipeline.py mixed apply result
assert "Applied 1 Player Update" in rendered
assert "player specs" not in rendered.lower()

# tests/test_logger.py
assert "Reviewed Player Update" in markdown
assert "Reviewed Player Update" in html
assert "Reviewed player spec" not in markdown
assert "Reviewed player spec" not in html
```

Add a CLI help test using the existing `sys.argv` pattern:

```python
def test_players_help_uses_player_update_language(monkeypatch, capsys):
    import run

    monkeypatch.setattr(run.sys, "argv", ["run.py", "players", "--help"])
    with pytest.raises(SystemExit) as exc_info:
        run.main()

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "Validate or apply revision-scoped Player Updates" in output
    assert "Validate Player Updates against the pristine base" in output
    assert "Generate an incomplete Player Update from an issue event" in output
    assert "Apply reviewed Player Updates to an EDIT file" in output
```

- [ ] **Step 2: Run the focused copy tests and verify red**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_generate_player_draft.py::test_generated_draft_validation_reports_exact_missing_human_fields \
  tests/test_run_pipeline.py::test_players_apply_mixed_success_and_mutation_failure_persists_verified_success \
  tests/test_run_pipeline.py::test_players_help_uses_player_update_language \
  tests/test_logger.py -q
```

Expected: failures cite the old “Player-spec,” “player specs,” and “Reviewed player spec” strings.

- [ ] **Step 3: Replace terminal copy without renaming internals**

Use these messages in `run.py`:

```text
Player Updates: active=...
Player Update validation failed: incomplete draft ...
Player Update validation failed: N current active update(s) are invalid.
Applying Player Updates failed: N unexpected mutation error(s); ...
No Player Update changes to apply; no backup or output was written.
Input EDIT file changed while Player Updates were being processed.
Applied N Player Update(s) to ...
```

For the final success message, choose singular/plural deterministically:

```python
update_label = "Player Update" if len(audit_records) == 1 else "Player Updates"
print(
    f"Applied {len(audit_records)} {update_label} to {output_path}. "
    f"Backup: {backup_path}"
)
```

Update argparse help exactly to:

```python
"Validate or apply revision-scoped Player Updates"
"Validate Player Updates against the pristine base"
"Generate an incomplete Player Update from an issue event"
"Apply reviewed Player Updates to an EDIT file"
```

Keep comments, docstrings, imports, exception names, functions, variables, and result reason/status values unchanged because they are internal contracts.

- [ ] **Step 4: Replace report source labels only**

In Markdown and HTML report rows, replace:

```text
Reviewed player spec
```

with:

```text
Reviewed Player Update
```

Keep audit classifiers, transfer types `player_spec_create`/`player_spec_update`, metrics keys, report section structure, and JSONL records unchanged.

- [ ] **Step 5: Run focused CLI, engine, and logger tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_generate_player_draft.py \
  tests/test_run_pipeline.py tests/test_logger.py tests/test_player_specs.py -q
```

Expected: all tests pass; only presentation strings changed.

- [ ] **Step 6: Run final terminology consistency checks**

Use the repository Grep tool for case-insensitive `player[- ]spec|player specification` across:

```text
README.md
.github/ISSUE_TEMPLATE/player-spec.yml
.github/workflows/generate-player-spec.yml
.github/workflows/player-spec-pr.yml
.github/workflows/ci.yml
run.py
editor/logger.py
```

Expected remaining matches are limited to approved technical identifiers, literal paths, labels, job IDs, temporary filenames, comments, or docstrings. No heading, Issue Form title/description, workflow display name, PR/issue message, CLI help/output, or report cell contains the jargon.

Then use Grep for `Player Change|Player Edit|Player Data Update` across the same surfaces. Expected: no matches.

- [ ] **Step 7: Run the full suite and smoke commands**

Run:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python run.py players --help
.venv/bin/python run.py players validate
```

Expected:

- full suite passes;
- help displays only Player Update public terminology;
- reviewed Dastan and Marco updates validate against the bundled base;
- no save mutation occurs during validation.

- [ ] **Step 8: Commit Task 3**

```bash
git add run.py editor/logger.py tests/test_generate_player_draft.py \
  tests/test_run_pipeline.py tests/test_logger.py
git commit -m "fix(players): clarify public update messages"
```
