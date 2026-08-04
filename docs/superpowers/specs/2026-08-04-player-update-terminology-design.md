# Player Update Public Terminology Design

## Goal

Replace developer-facing “player spec” language in public copy with “Player Update,” so international players can understand the feature without knowing that “spec” means “specification.” Preserve the existing technical contract and behavior.

## Terminology

Use **Player Update** as the public feature name.

| Current public copy | Replacement |
|---|---|
| Player-spec contributions | Player Updates |
| Player specification request | Player Update Request |
| Draft player specification | Draft Player Update |
| Validate player specifications | Validate Player Updates |
| Apply reviewed player specs | Apply Reviewed Player Updates |
| `create` | Keep `create`; explain nearby that it adds a new player |
| `update` | Keep `update`; explain nearby that it changes an existing player |

Issue Form introduction:

> Request a new player or an update to an existing player. A maintainer will review the data before it is added.

## Public Surfaces

Update only user-visible wording in:

- README contribution instructions and command descriptions;
- the Issue Form name, description, title prefix, field descriptions, and operation labels;
- GitHub Actions display names, step names, draft PR title/body, and issue comments;
- CLI help, validation errors, progress summaries, and success messages;
- audit report headings or labels that are intended for end users.

Use “player update” consistently. Do not alternate with “player change,” “player edit,” or “player data update.”

## Technical Compatibility

Keep all technical identifiers unchanged:

- Python modules, classes, functions, exceptions, and variables containing `player_spec`;
- JSON schema keys and `operation` values `create` and `update`;
- CLI commands `players validate`, `players apply`, and `players generate-draft`;
- paths such as `players/`, `.github/ISSUE_TEMPLATE/player-spec.yml`, and workflow filenames;
- GitHub labels such as `player-spec` and `generate-player-draft`;
- branch names, job IDs, test names, and machine-readable output fields.

GitHub Issue Form dropdown options are submitted exactly as displayed; they do not have separate labels and values. Keep the literal `create` and `update` options required by the parser, and explain their plain-language meanings in the field description or adjacent Markdown.

## Behavior and Security

This is a copy-only change. It must not alter validation, generation, mutation, permissions, event parsing, branch naming, path guards, or workflow triggers. Machine-readable output and trusted parser headings remain unchanged unless tests prove a user-visible label can change without changing the parsed value.

## Verification

- Static tests assert the new public wording on README, Issue Form, workflows, CLI help, and user-visible report labels.
- Existing parser, generator, workflow-security, player validation, and full test suites remain green.
- A repository search confirms no unintended public “player spec” or “player specification” wording remains.
- A second search confirms technical identifiers and compatibility-sensitive labels remain unchanged.
