import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
import pytest


FORM_PATH = Path(".github/ISSUE_TEMPLATE/player-update.yml")
WORKFLOW_PATH = Path(".github/workflows/generate-player-update.yml")
CI_PATH = Path(".github/workflows/ci.yml")
PLAYER_TARGET_PATH = Path(".github/workflows/validate-player-update-pr.yml")
SYNC_WORKFLOW_PATHS = (
    Path(".github/workflows/sync-fast.yml"),
    Path(".github/workflows/sync-deep.yml"),
)
INSTALLER_WORKFLOW_PATH = Path(".github/workflows/build-installer.yml")
INSTALLER_SPEC_PATH = Path("FLDailyEditInstaller.spec")
PYPROJECT_PATH = Path("pyproject.toml")
README_PATH = Path("README.md")

INSTALLER_URL = (
    "https://github.com/gvoze32/fldailyedit/releases/download/latest/"
    "FLDailyEditInstaller.exe"
)
FAST_ZIP_URL = (
    "https://github.com/gvoze32/fldailyedit/releases/download/latest/"
    "fldailyedit-fl2026-fast.zip"
)
DEEP_ZIP_URL = (
    "https://github.com/gvoze32/fldailyedit/releases/download/latest/"
    "fldailyedit-fl2026-deep.zip"
)
README_INSTALLER_CONTRACTS = (
    (
        Path("README.md"),
        "## Windows installer",
        "## Roadmap / Complete for now",
        "The installer interface is currently available in English only.",
        "The initial executable is unsigned, so Windows SmartScreen may display a warning.",
        "Detection for vanilla eFootball PES 2021 is present, but installation remains disabled until a matching validated base is published.",
    ),
    (
        Path("README.id.md"),
        "## Installer Windows",
        "## Roadmap / Selesai untuk saat ini",
        "Antarmuka installer saat ini hanya tersedia dalam bahasa Inggris.",
        "Executable awal belum ditandatangani, sehingga Windows SmartScreen mungkin menampilkan peringatan.",
        "Deteksi untuk vanilla eFootball PES 2021 sudah tersedia, tetapi pemasangan tetap dinonaktifkan hingga base tervalidasi yang sesuai diterbitkan.",
    ),
    (
        Path("README.zh.md"),
        "## Windows 安装程序",
        "## 路线图 / 当前已完成",
        "安装程序界面目前仅提供英语版本。",
        "初始可执行文件尚未签名，因此 Windows SmartScreen 可能会显示警告。",
        "程序可以检测原版 eFootball PES 2021，但在发布匹配且经过验证的基础存档前，安装功能将保持禁用。",
    ),
    (
        Path("README.ar.md"),
        "## مُثبِّت Windows",
        "## خارطة الطريق / مكتملة حاليًا",
        "تتوفر واجهة المُثبِّت حاليًا باللغة الإنجليزية فقط.",
        "الملف التنفيذي الأولي غير موقّع، لذلك قد يعرض Windows SmartScreen تحذيرًا.",
        "يتوفر اكتشاف vanilla eFootball PES 2021، لكن يظل التثبيت معطّلًا حتى نشر قاعدة أساسية متحقّق منها ومطابقة.",
    ),
    (
        Path("README.ru.md"),
        "## Установщик Windows",
        "## План развития / Пока завершён",
        "Интерфейс установщика пока доступен только на английском языке.",
        "Первоначальный исполняемый файл не подписан, поэтому Windows SmartScreen может показать предупреждение.",
        "Обнаружение vanilla eFootball PES 2021 поддерживается, но установка остаётся отключённой до публикации подходящей проверенной базы.",
    ),
    (
        Path("README.it.md"),
        "## Programma di installazione per Windows",
        "## Roadmap / Completa per ora",
        "L'interfaccia del programma di installazione è attualmente disponibile solo in inglese.",
        "L'eseguibile iniziale non è firmato, quindi Windows SmartScreen potrebbe mostrare un avviso.",
        "Il rilevamento di eFootball PES 2021 vanilla è disponibile, ma l'installazione rimane disabilitata finché non viene pubblicata una base convalidata corrispondente.",
    ),
    (
        Path("README.pt.md"),
        "## Instalador para Windows",
        "## Roteiro / Concluído por enquanto",
        "A interface do instalador está disponível somente em inglês no momento.",
        "O executável inicial não é assinado, portanto o Windows SmartScreen pode exibir um aviso.",
        "A detecção do eFootball PES 2021 vanilla está presente, mas a instalação permanece desativada até que uma base validada correspondente seja publicada.",
    ),
    (
        Path("README.es.md"),
        "## Instalador para Windows",
        "## Hoja de ruta / Completada por ahora",
        "La interfaz del instalador actualmente solo está disponible en inglés.",
        "El ejecutable inicial no está firmado, por lo que Windows SmartScreen puede mostrar una advertencia.",
        "La detección de eFootball PES 2021 vanilla está disponible, pero la instalación permanece desactivada hasta que se publique una base validada correspondiente.",
    ),
    (
        Path("README.tr.md"),
        "## Windows yükleyici",
        "## Yol Haritası / Şimdilik tamamlandı",
        "Yükleyici arayüzü şu anda yalnızca İngilizce olarak sunulmaktadır.",
        "İlk yürütülebilir dosya imzasızdır; bu nedenle Windows SmartScreen bir uyarı gösterebilir.",
        "Vanilla eFootball PES 2021 algılanabilir, ancak eşleşen doğrulanmış bir temel yayımlanana kadar kurulum devre dışı kalır.",
    ),
    (
        Path("README.de.md"),
        "## Windows-Installationsprogramm",
        "## Roadmap / Vorerst abgeschlossen",
        "Die Benutzeroberfläche des Installationsprogramms ist derzeit nur auf Englisch verfügbar.",
        "Die erste ausführbare Datei ist nicht signiert, daher kann Windows SmartScreen eine Warnung anzeigen.",
        "Vanilla eFootball PES 2021 wird erkannt, die Installation bleibt jedoch deaktiviert, bis eine passende validierte Basis veröffentlicht wird.",
    ),
    (
        Path("README.fr.md"),
        "## Programme d’installation Windows",
        "## Feuille de route / Terminée pour l’instant",
        "L’interface du programme d’installation est actuellement disponible uniquement en anglais.",
        "L’exécutable initial n’est pas signé, Windows SmartScreen peut donc afficher un avertissement.",
        "La détection d’eFootball PES 2021 vanilla est disponible, mais l’installation reste désactivée jusqu’à la publication d’une base validée correspondante.",
    ),
)




EXPECTED_FIELDS = (
    ("dropdown", "operation", "Operation"),
    ("input", "player_name", "Player name"),
    ("input", "pes_retro_stats_profile", "Pes Retro Stats profile"),
    ("input", "current_team", "Current team"),
    ("input", "effective_date", "Effective date"),
    ("textarea", "proof_urls", "Proof URLs"),
    ("textarea", "contributor_notes", "Contributor notes"),
    ("checkboxes", "confirmations", "Confirmations"),
)
EXPECTED_CONFIRMATIONS = (
    "I supplied one canonical Pes Retro Stats player profile.",
    "I understand autofilled PES values are unapproved proposals.",
    "I understand a maintainer must review the draft PR.",
)


def _field_blocks(text: str) -> list[tuple[str, str]]:
    body = text.split("\nbody:\n", 1)[1]
    matches = list(re.finditer(r"(?m)^  - type: ([a-z]+)$", body))
    return [
        (match.group(1), body[match.start() : next_start])
        for match, next_start in zip(
            matches,
            [following.start() for following in matches[1:]] + [len(body)],
            strict=True,
        )
    ]


def _field_id(block: str) -> str:
    match = re.search(r"(?m)^    id: ([a-z0-9_]+)$", block)
    assert match is not None
    return match.group(1)


def _field_label(block: str) -> str:
    match = re.search(r"(?m)^      label: (.+)$", block)
    assert match is not None
    return match.group(1)


def _workflow_step_script(name: str) -> str:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    match = re.search(
        rf"(?ms)^      - name: {re.escape(name)}$\n"
        r".*?^        run: \|\n(?P<script>.*?)(?=^      - name:|\Z)",
        text,
    )
    assert match is not None
    return textwrap.dedent(match.group("script"))


def _target_workflow_step_script(name: str) -> str:
    text = PLAYER_TARGET_PATH.read_text(encoding="utf-8")
    match = re.search(
        rf"(?ms)^      - name: {re.escape(name)}$\n"
        r".*?^        run: \|\n(?P<script>.*?)(?=^      - name:|\Z)",
        text,
    )
    assert match is not None
    return textwrap.dedent(match.group("script"))

def _bash_command() -> list[str]:
    if os.name != "nt":
        return ["bash", "-c"]

    bash_path = shutil.which("bash")
    if bash_path is not None:
        resolved_bash = Path(bash_path).resolve()
        system_root = Path(
            os.environ.get("SystemRoot", r"C:\Windows")
        ).resolve()
        if resolved_bash.parent.as_posix().casefold() != (
            (system_root / "System32").as_posix().casefold()
        ):
            return [str(resolved_bash), "-c"]

    git_path = shutil.which("git")
    if git_path is not None:
        git_root = Path(git_path).resolve().parent.parent
        for candidate in (
            git_root / "bin" / "bash.exe",
            git_root / "usr" / "bin" / "bash.exe",
        ):
            if candidate.is_file():
                return [str(candidate), "-c"]

    raise FileNotFoundError("Git Bash executable not found")


def _workflow_heredoc_script(marker: str) -> str:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    start = text.index(marker) + len(marker)
    end = text.index("\n          PY", start)
    return textwrap.dedent(text[start:end])


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _remote_branch_fixture(
    tmp_path: Path,
    *,
    local_spec: str,
    remote_spec: str,
    extra_path: bool = False,
    advance_default_and_shallow_checkout: bool = False,
) -> tuple[Path, dict[str, str]]:
    origin = tmp_path / "origin.git"
    repository = tmp_path / "repository"
    runner_temp = tmp_path / "runner"
    output_path = tmp_path / "github-output"
    branch_name = "player-draft/issue-42"
    spec_path = Path("players/test-player.json")

    _git(tmp_path, "init", "--bare", str(origin))
    _git(tmp_path, "init", "-b", "main", str(repository))
    _git(repository, "config", "user.name", "Workflow Test")
    _git(repository, "config", "user.email", "workflow@example.invalid")
    (repository / "base.txt").write_text("base\n", encoding="utf-8")
    _git(repository, "add", "base.txt")
    _git(repository, "commit", "-m", "base")
    _git(repository, "remote", "add", "origin", str(origin))
    _git(repository, "push", "--set-upstream", "origin", "main")

    _git(repository, "switch", "--create", branch_name)
    (repository / spec_path).parent.mkdir(parents=True)
    (repository / spec_path).write_bytes(remote_spec.encode("utf-8"))
    _git(repository, "add", "--", spec_path.as_posix())
    if extra_path:
        (repository / "unexpected.txt").write_text("unexpected\n", encoding="utf-8")
        _git(repository, "add", "unexpected.txt")
    _git(repository, "commit", "-m", "remote draft")
    _git(repository, "push", "--set-upstream", "origin", branch_name)

    _git(repository, "switch", "main")
    if advance_default_and_shallow_checkout:
        (repository / "advance.txt").write_text("advanced\n", encoding="utf-8")
        _git(repository, "add", "advance.txt")
        _git(repository, "commit", "-m", "advance default")
        _git(repository, "push", "origin", "main")
        runner_repository = tmp_path / "runner-repository"
        _git(
            tmp_path,
            "clone",
            "--depth",
            "1",
            "--branch",
            "main",
            f"file://{origin}",
            str(runner_repository),
        )
    else:
        runner_repository = repository

    (runner_repository / spec_path).parent.mkdir(parents=True)
    (runner_repository / spec_path).write_bytes(local_spec.encode("utf-8"))
    runner_temp.mkdir()
    environment = os.environ.copy()
    environment.update(
        {
            "BRANCH_NAME": branch_name,
            "GITHUB_OUTPUT": str(output_path),
            "RUNNER_TEMP": str(runner_temp),
            "SPEC_PATH": spec_path.as_posix(),
        }
    )
    return runner_repository, environment


def test_readme_uses_player_update_language_for_public_contributions():
    text = README_PATH.read_text(encoding="utf-8")
    assert "## Player Updates" in text
    assert "Validate all Player Updates against the pristine base" in text
    assert "Apply reviewed Player Updates explicitly to one save" in text
    assert "player update issue form" in text
    assert "CI accepts a Player Update only when" in text
    assert "## Player-spec contributions" not in text
    lifecycle = " ".join(
        text.split("### Revision lifecycle", 1)[1]
        .split("Common `run` options:", 1)[0]
        .split()
    )
    assert "Keep historical Player Updates in `players/`" in lifecycle
    assert "An active Player Update whose `applies_to` list" in lifecycle
    assert "revision only when the Player Update still applies" in lifecycle
    for retired_phrase in ("historical specs", "active spec", "the spec"):
        assert retired_phrase not in lifecycle
    for technical_literal in (
        "`players/`",
        "`applies_to`",
        "`needs_review`",
        "`upstreamed`",
        "`retired`",
    ):
        assert technical_literal in lifecycle


def test_localized_readmes_are_installer_first_and_keep_public_manual_fallbacks():
    for path, installer_heading, roadmap_heading, ui_copy, warning, pes_copy in (
        README_INSTALLER_CONTRACTS
    ):
        text = path.read_text(encoding="utf-8")

        assert text.count(INSTALLER_URL) == 1, path
        assert text.count(FAST_ZIP_URL) == 1, path
        assert text.count(DEEP_ZIP_URL) == 1, path
        assert installer_heading in text, path
        assert ui_copy in text, path
        assert warning in text, path
        assert pes_copy in text, path

        roadmap = text.split(roadmap_heading, 1)[1].split("\n## ", 1)[0]
        numbered_items = re.findall(r"(?m)^\d+\. \*\*", roadmap)
        assert numbered_items == [], path
        assert "installer" not in roadmap.casefold(), path


def test_workflows_use_player_update_copy_on_public_surfaces():
    generator = WORKFLOW_PATH.read_text(encoding="utf-8")
    target = PLAYER_TARGET_PATH.read_text(encoding="utf-8")
    ci = CI_PATH.read_text(encoding="utf-8")

    assert generator.startswith("name: Generate Player Update Draft\n")
    assert "- name: Generate Player Update" in generator
    assert 'COMMENT_BODY="Draft Player Update: $pr_url"' in generator
    assert '--title "Draft Player Update: $PLAYER_NAME"' in generator
    assert "complete and CI-verified" in generator
    assert "requires explicit human approval" in generator
    assert (
        "community-weighted estimate, not an official in-game rating"
        in generator
    )

    assert target.startswith("name: Validate Player Update pull request\n")
    assert "name: Validate trusted Player Update boundary" in target
    assert "- name: Materialize validated Player Update" in target
    assert "- name: Validate materialized Player Update" in target
    assert "- name: Validate Player Updates" in ci


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


def test_issue_form_matches_the_generator_heading_contract_exactly():
    text = FORM_PATH.read_text(encoding="utf-8")
    fields = _field_blocks(text)

    assert tuple(
        (field_type, _field_id(block), _field_label(block))
        for field_type, block in fields
    ) == EXPECTED_FIELDS
    assert 'labels: ["player-spec"]' in text
    assert "generate-player-draft" not in text
    assert "draft" in text.lower()
    assert (
        "description: Request a new player or an update to an existing player. "
        "A maintainer will review the data before it is added."
        in text
    )


def test_issue_form_requires_inputs_and_exact_rendered_confirmations():
    text = FORM_PATH.read_text(encoding="utf-8")
    blocks = {_field_id(block): block for _type, block in _field_blocks(text)}

    for field_id in (
        "operation",
        "player_name",
        "pes_retro_stats_profile",
        "current_team",
        "effective_date",
        "proof_urls",
    ):
        assert re.search(
            r"(?m)^    validations:\n      required: true$", blocks[field_id]
        )
    assert "validations:" not in blocks["contributor_notes"]
    assert re.findall(r"(?m)^        - (create|update)$", blocks["operation"]) == [
        "create",
        "update",
    ]

    confirmation_options = re.findall(
        r"(?m)^      - label: (.+)\n        required: true$",
        blocks["confirmations"],
    )
    assert tuple(confirmation_options) == EXPECTED_CONFIRMATIONS
    assert tuple(f"- [X] {label}" for label in confirmation_options) == (
        "- [X] I supplied one canonical Pes Retro Stats player profile.",
        "- [X] I understand autofilled PES values are unapproved proposals.",
        "- [X] I understand a maintainer must review the draft PR.",
    )


def test_generate_workflow_is_label_gated_and_minimally_privileged():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "types: [labeled]" in text
    assert "github.event.label.name == 'generate-player-draft'" in text
    assert "contents: write" in text
    assert "pull-requests: write" in text
    assert "issues: write" in text
    assert "concurrency: player-draft-${{ github.event.issue.number }}" in text
    assert "pull_request_target" not in text


def test_generate_workflow_uses_trusted_event_file_and_exact_machine_output():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "actions/checkout@v7" in text
    assert "ref: ${{ github.event.repository.default_branch }}" in text
    assert "persist-credentials: true" in text
    assert "fetch-depth: 0" in text
    assert "actions/setup-python@v7" in text
    assert 'python-version: "3.13"' in text
    assert (
        'python run.py players generate-draft --event "$GITHUB_EVENT_PATH" '
        '--output-dir players'
        in text
    )
    assert "if len(lines) != 2:" in text
    assert 'lines[0].startswith("SPEC_PATH=")' in text
    assert 'lines[1].startswith("PLAYER_NAME=")' in text
    assert text.index('lines[0].startswith("SPEC_PATH=")') < text.index(
        'lines[1].startswith("PLAYER_NAME=")'
    )
    assert "os.environ[\"GITHUB_OUTPUT\"]" in text
    assert 'f"{name}<<{delimiter}\\n{value}\\n{delimiter}\\n"' in text


def test_workflows_use_latest_official_action_majors():
    versions: dict[str, set[str]] = {}

    for path in Path(".github/workflows").glob("*.yml"):
        text = path.read_text(encoding="utf-8")
        for action, version in re.findall(r"uses:\s+actions/([^@\s]+)@(\S+)", text):
            versions.setdefault(action, set()).add(version)

    assert versions == {
        "checkout": {"v7"},
        "download-artifact": {"v8"},
        "setup-python": {"v7"},
        "upload-artifact": {"v7"},
    }


def test_generate_workflow_compiles_decrypter_before_generation():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    compile_step = """      - name: Compile pesXdecrypter binaries
        if: steps.existing.outputs.pr_url == ''
        shell: bash
        run: |
          set -euo pipefail
          make -C vendor/pesXdecrypter clean
          make -C vendor/pesXdecrypter
          chmod +x vendor/pesXdecrypter/decrypter21 vendor/pesXdecrypter/encrypter21
"""

    assert compile_step in text
    assert text.index(compile_step) < text.index("      - name: Generate Player Update")


def test_legacy_user_facing_paths_are_absent():
    assert not Path(".github/ISSUE_TEMPLATE/player-spec.yml").exists()
    assert not Path(".github/workflows/generate-player-spec.yml").exists()
    assert not Path(".github/workflows/player-spec-pr.yml").exists()


def test_generate_workflow_uses_safe_branch_and_one_idempotent_draft_pr():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert 'isinstance(issue_number, bool)' in text
    assert 'isinstance(issue_number, int)' in text
    assert 'issue_number <= 0' in text
    assert "player-draft/issue-${{ steps.event.outputs.issue_number }}" in text
    assert (
        'gh api --method GET "repos/$GITHUB_REPOSITORY/pulls"' in text
    )
    assert '-f "head=$GITHUB_REPOSITORY_OWNER:$BRANCH_NAME"' in text
    assert "-f state=all" in text
    assert 'head.get("repo", {}).get("full_name") != repository' in text
    assert 'head.get("ref") != branch_name' in text
    assert 'base.get("repo", {}).get("full_name") != repository' in text
    assert 'default_branch = os.environ["DEFAULT_BRANCH"]' in text
    assert 'base.get("ref") != default_branch' in text
    assert 'gh pr list --head' not in text
    assert 'git switch --create "$BRANCH_NAME"' in text
    assert 'git add -- "$SPEC_PATH"' in text
    assert 'git diff --cached --name-only' in text
    assert 'git push --set-upstream origin "$BRANCH_NAME"' in text
    assert "gh pr create" in text
    assert "--draft" in text
    assert '--title "Draft Player Update: $PLAYER_NAME"' in text
    assert 'gh issue comment "$ISSUE_NUMBER" --body "$COMMENT_BODY"' in text


def test_existing_pr_parser_rejects_a_same_repo_pr_to_the_wrong_base(tmp_path):
    response_path = tmp_path / "pulls.json"
    response_path.write_text(
        json.dumps(
            [
                {
                    "html_url": "https://github.com/example/project/pull/7",
                    "head": {
                        "ref": "player-draft/issue-42",
                        "repo": {"full_name": "example/project"},
                    },
                    "base": {
                        "ref": "release",
                        "repo": {"full_name": "example/project"},
                    },
                }
            ]
        ),
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment.update(
        {
            "BRANCH_NAME": "player-draft/issue-42",
            "DEFAULT_BRANCH": "main",
            "GITHUB_OUTPUT": str(tmp_path / "github-output"),
            "GITHUB_REPOSITORY": "example/project",
        }
    )

    result = subprocess.run(
        [sys.executable, "-", str(response_path)],
        input=_workflow_heredoc_script(
            "            python - \"$pr_response\" <<'PY'\n"
        ),
        env=environment,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "refusing a cross-repository or mismatched pull request" in result.stderr


def test_generate_workflow_recovers_only_an_exact_matching_base_repo_branch():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert (
        'git ls-remote --exit-code --heads origin "refs/heads/$BRANCH_NAME"'
        in text
    )
    assert 'refs/heads/$BRANCH_NAME:refs/remotes/origin/$BRANCH_NAME' in text
    assert 'git diff --name-only "HEAD...refs/remotes/origin/$BRANCH_NAME"' in text
    assert '[[ "$changed_paths" != "$SPEC_PATH" ]]' in text
    assert 'git show "refs/remotes/origin/$BRANCH_NAME:$SPEC_PATH"' in text
    assert 'cmp -- "$SPEC_PATH" "$remote_spec"' in text
    assert "steps.remote.outputs.branch_exists == 'false'" in text


def test_remote_branch_recovery_accepts_the_exact_generated_spec(tmp_path):
    repository, environment = _remote_branch_fixture(
        tmp_path,
        local_spec='{"name": "same"}\n',
        remote_spec='{"name": "same"}\n',
    )

    result = subprocess.run(
        [
            *_bash_command(),
            _workflow_step_script("Verify an existing base-repository branch"),
        ],
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert Path(environment["GITHUB_OUTPUT"]).read_text(encoding="utf-8") == (
        "branch_exists=true\n"
    )


def test_remote_branch_recovery_survives_an_advanced_default_from_shallow_checkout(
    tmp_path,
):
    repository, environment = _remote_branch_fixture(
        tmp_path,
        local_spec='{"name": "same"}\n',
        remote_spec='{"name": "same"}\n',
        advance_default_and_shallow_checkout=True,
    )
    shallow_marker = repository / ".git/shallow"
    assert shallow_marker.is_file()
    assert "fetch-depth: 0" in WORKFLOW_PATH.read_text(encoding="utf-8")
    _git(repository, "fetch", "--unshallow", "origin")
    assert not shallow_marker.exists()

    result = subprocess.run(
        [
            *_bash_command(),
            _workflow_step_script("Verify an existing base-repository branch"),
        ],
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert Path(environment["GITHUB_OUTPUT"]).read_text(encoding="utf-8") == (
        "branch_exists=true\n"
    )


def test_remote_branch_recovery_rejects_a_different_spec_blob(tmp_path):
    repository, environment = _remote_branch_fixture(
        tmp_path,
        local_spec='{"name": "new"}\n',
        remote_spec='{"name": "old"}\n',
    )

    result = subprocess.run(
        [
            *_bash_command(),
            _workflow_step_script("Verify an existing base-repository branch"),
        ],
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "remote issue branch spec differs from generated spec" in result.stderr


def test_remote_branch_recovery_rejects_an_additional_changed_path(tmp_path):
    repository, environment = _remote_branch_fixture(
        tmp_path,
        local_spec='{"name": "same"}\n',
        remote_spec='{"name": "same"}\n',
        extra_path=True,
    )

    result = subprocess.run(
        [
            *_bash_command(),
            _workflow_step_script("Verify an existing base-repository branch"),
        ],
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "remote issue branch changes unexpected paths" in result.stderr


def test_generate_workflow_does_not_put_untrusted_event_data_in_shell_structure():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")

    for unsafe in (
        "github.event.issue.body",
        "github.event.issue.title",
        "github.actor",
        "GITHUB_ENV",
        "eval ",
        "eval\n",
    ):
        assert unsafe not in text

    run_blocks = re.findall(
        r"(?ms)^        run: \|\n(.*?)(?=^      - name:|\Z)", text
    )
    assert run_blocks
    assert all("${{ github.event.issue" not in block for block in run_blocks)
    assert text.count("shell: bash") == len(run_blocks)


def test_ci_keeps_the_python_matrix_and_validates_specs_after_native_build():
    text = CI_PATH.read_text(encoding="utf-8")
    matrix_job = text.split("\n  test:\n", 1)[1]
    native_build = "make -C vendor/pesXdecrypter"
    validator = "python run.py players validate"

    assert 'python-version: ["3.10", "3.11", "3.12", "3.13"]' in matrix_job
    assert "os: [ubuntu-latest, macos-latest]" in matrix_job
    assert native_build in matrix_job
    assert validator in matrix_job
    assert matrix_job.index(native_build) < matrix_job.index(validator)
    assert "\n  player-spec-pr:\n" not in text


def _target_event_fixture() -> dict[str, object]:
    return {
        "number": 7,
        "pull_request": {
            "base": {"sha": "a" * 40},
            "head": {
                "sha": "b" * 40,
                "repo": {"full_name": "owner/repo"},
                "ref": "player-draft/issue-7",
            },
        },
    }


def _run_target_event_parser(
    tmp_path: Path,
    event: dict[str, object],
) -> tuple[subprocess.CompletedProcess[str], Path]:
    event_path = tmp_path / "event.json"
    output_path = tmp_path / "github-output"
    event_path.write_text(json.dumps(event), encoding="utf-8")
    environment = os.environ.copy()
    environment.update(
        {
            "GITHUB_EVENT_PATH": str(event_path),
            "GITHUB_OUTPUT": str(output_path),
        }
    )
    result = subprocess.run(
        [
            *_bash_command(),
            _target_workflow_step_script("Read trusted pull request coordinates"),
        ],
        env=environment,
        capture_output=True,
        text=True,
    )
    return result, output_path


def test_target_event_parser_emits_strict_head_repository_and_ref(
    tmp_path: Path,
) -> None:
    result, output_path = _run_target_event_parser(
        tmp_path,
        _target_event_fixture(),
    )

    assert result.returncode == 0, result.stderr
    assert output_path.read_text(encoding="utf-8").splitlines() == [
        "pr_number=7",
        f"base_sha={'a' * 40}",
        f"head_sha={'b' * 40}",
        "head_repo=owner/repo",
        "head_ref=player-draft/issue-7",
    ]


@pytest.mark.parametrize(
    ("part", "value"),
    [
        pytest.param("repo", None, id="missing-repository"),
        pytest.param("repo", "owner", id="repository-without-owner-slash"),
        pytest.param("repo", "owner/repo\nINJECTED=1", id="repository-control"),
        pytest.param("ref", "", id="empty-ref"),
        pytest.param("ref", 42, id="non-string-ref"),
        pytest.param("ref", "player/update\nINJECTED=1", id="ref-control"),
    ],
)
def test_target_event_parser_rejects_untrusted_head_coordinates(
    tmp_path: Path,
    part: str,
    value: object,
) -> None:
    event = _target_event_fixture()
    pull_request = event["pull_request"]
    assert isinstance(pull_request, dict)
    head = pull_request["head"]
    assert isinstance(head, dict)
    if part == "repo":
        repo = head["repo"]
        assert isinstance(repo, dict)
        repo["full_name"] = value
    else:
        head["ref"] = value

    result, output_path = _run_target_event_parser(tmp_path, event)

    assert result.returncode != 0
    assert output_path.read_text(encoding="utf-8") == ""


def test_target_workflow_runs_origin_check_after_one_materialized_json() -> None:
    text = PLAYER_TARGET_PATH.read_text(encoding="utf-8")
    materialize = 'git show "${HEAD_SHA}:${PLAYER_PATH}" > "$PLAYER_PATH"'
    origin_check = (
        "python tools/check_player_proposal_origin.py "
        '--spec "$PLAYER_PATH" '
        '--base-repo "$GITHUB_REPOSITORY" '
        '--head-repo "$HEAD_REPO" '
        '--head-ref "$HEAD_REF"'
    )
    normalized_text = re.sub(r"\\\n\s*", " ", text)
    validator = "python run.py players validate"

    assert text.count(materialize) == 1
    assert origin_check in normalized_text
    assert "HEAD_REPO: ${{ steps.event.outputs.head_repo }}" in text
    assert "HEAD_REF: ${{ steps.event.outputs.head_ref }}" in text
    assert text.index(materialize) < text.index(
        "python tools/check_player_proposal_origin.py"
    )
    assert text.index("python tools/check_player_proposal_origin.py") < text.index(
        validator
    )


def test_generated_draft_pr_body_requires_explicit_human_review_copy() -> None:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    body_start = text.index("printf -v PR_BODY")
    body_end = text.index("\n          pr_url=", body_start)
    pr_body = text[body_start:body_end]

    assert "complete and CI-verified" in pr_body
    assert "requires explicit human approval" in pr_body
    assert (
        "community-weighted estimate, not an official in-game rating"
        in pr_body
    )
    assert "incomplete Player Update" not in pr_body


def test_target_workflow_is_base_owned_read_only_and_runs_for_pull_requests():
    text = PLAYER_TARGET_PATH.read_text(encoding="utf-8")

    assert "\n  pull_request_target:\n    branches: [main]\n" in text
    assert "permissions:\n  contents: read\n" in text
    assert "contents: write" not in text
    assert "pull-requests:" not in text
    assert "secrets." not in text
    assert "ref: ${{ github.event.pull_request.base.sha }}" in text
    assert "fetch-depth: 0" in text
    assert "persist-credentials: true" in text
    assert 'Path(os.environ["GITHUB_EVENT_PATH"])' in text
    assert 're.fullmatch(r"[0-9a-f]{40}", value)' in text
    assert "isinstance(number, bool)" in text
    assert "number <= 0" in text


def test_target_workflow_fetches_numeric_pull_ref_and_verifies_head_sha():
    text = PLAYER_TARGET_PATH.read_text(encoding="utf-8")

    assert (
        'git fetch --no-tags origin '
        '"refs/pull/$PR_NUMBER/head:$HEAD_REF"'
    ) in text
    assert 'HEAD_REF="refs/remotes/origin/pull/$PR_NUMBER/head"' in text
    assert 'actual_head="$(git rev-parse "$HEAD_REF^{commit}")"' in text
    assert '[[ "$actual_head" != "$HEAD_SHA" ]]' in text
    assert 'actual_base="$(git rev-parse "HEAD^{commit}")"' in text
    assert '[[ "$actual_base" != "$BASE_SHA" ]]' in text
    assert (
        'git diff --name-status --diff-filter=ACDMRTUXB '
        '"$BASE_SHA...$HEAD_SHA" -- > "$CHANGES_FILE"'
    ) in text


def test_target_workflow_runs_base_guard_before_materializing_one_head_blob():
    text = PLAYER_TARGET_PATH.read_text(encoding="utf-8")
    guard = (
        'python tools/check_player_spec_pr.py --changes-file "$CHANGES_FILE"'
    )
    materialize = 'git show "${HEAD_SHA}:${PLAYER_PATH}" > "$PLAYER_PATH"'
    native_build = "make -C vendor/pesXdecrypter clean"
    validator = "python run.py players validate"

    assert guard in text
    assert materialize in text
    assert native_build in text
    assert validator in text
    assert text.index(guard) < text.index(materialize)
    assert text.index(materialize) < text.index(native_build)
    assert text.index(native_build) < text.index(validator)


def test_target_workflow_never_checks_out_or_executes_pull_request_head_code():
    target = PLAYER_TARGET_PATH.read_text(encoding="utf-8")
    ordinary_ci = CI_PATH.read_text(encoding="utf-8")
    generator = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "pull_request_target" in target
    assert "pull_request_target" not in ordinary_ci
    assert "pull_request_target" not in generator
    assert "github.event.issue" not in target
    assert "github.event.pull_request.head" not in target
    assert "git checkout" not in target
    assert "git switch" not in target
    assert "pip install" in target
    assert target.index("tools/check_player_spec_pr.py") < target.index(
        "pip install"
    )



def test_sync_workflows_read_revision_from_checked_out_manifest_without_literal_drift():
    for path in SYNC_WORKFLOW_PATHS:
        text = path.read_text(encoding="utf-8")
        assert 'json.load(open("data/base_manifest.json", encoding="utf-8"))["revision"]' in text
        assert 'BASE_REVISION="$(' in text
        assert '--base-revision "$BASE_REVISION"' in text
        assert '--base-revision fl26-u2.2-national-squads' not in text



def test_sync_workflows_validate_package_and_transfer_complete_channel_payloads():
    workflows = (
        (Path(".github/workflows/sync-fast.yml"), "Fast", "fast"),
        (Path(".github/workflows/sync-deep.yml"), "Deep", "deep"),
    )

    for path, display_channel, channel in workflows:
        text = path.read_text(encoding="utf-8")
        apply_position = text.index("          python run.py players apply")
        validation_position = text.index("      - name: Validate final save")
        package_position = text.index(
            f"      - name: Package public {display_channel} save"
        )
        payload_position = text.index("      - name: Upload release payload")
        report_position = text.index(
            "      - name: Upload Updated Save File & Visual Reports"
        )

        assert (
            apply_position
            < validation_position
            < package_position
            < payload_position
            < report_position
        )
        assert (
            text[validation_position:package_position]
            == "      - name: Validate final save\n"
            "        run: python run.py validate --edit-file output/EDIT00000000\n\n"
        )

        package = text[package_position:payload_position]
        assert "if: always()" not in package
        assert (
            "python tools/build_release_asset.py package \\\n"
            "            --save output/EDIT00000000 \\\n"
            "            --output-dir release-payload \\\n"
            "            --target-id fl26-u2.2-national-squads \\\n"
            '            --target-name "Football Life 2026 Update 2.2 + National Squads" \\\n'
            f"            --channel {channel} \\\n"
            '            --generated-at "$GENERATED_AT"'
        ) in package

        payload = text[payload_position:report_position]
        assert "if: always()" not in payload
        assert "uses: actions/upload-artifact@v7" in payload
        assert f"name: release-${{{{ github.run_id }}}}-{channel}" in payload
        assert "path: release-payload/" in payload
        assert "retention-days: 1" in payload
        assert (
            "        if: always()\n"
            "        uses: actions/upload-artifact@v7"
            in text[report_position:]
        )


def test_sync_workflows_publish_only_validated_payloads_under_one_catalog_lock():
    workflows = (
        (Path(".github/workflows/sync-fast.yml"), "Fast", "fast", "deep"),
        (Path(".github/workflows/sync-deep.yml"), "Deep", "deep", "fast"),
    )

    for path, display_channel, channel, other_channel in workflows:
        text = path.read_text(encoding="utf-8")
        sync, publish = text.split("\n  publish:\n", 1)

        assert "build_release_asset.py merge" not in sync
        assert "gh release" not in sync
        assert publish.startswith(
            f"    name: Publish {display_channel} save\n"
            "    needs: sync\n"
            "    runs-on: ubuntu-latest\n"
            "    permissions:\n"
            "      contents: write\n"
            "    concurrency:\n"
            "      group: fldailyedit-latest-release\n"
            "      cancel-in-progress: false\n"
        )
        assert "if: always()" not in publish

        download_position = publish.index("      - uses: actions/download-artifact@v8")
        merge_position = publish.index("      - name: Merge release catalog")
        release_position = publish.index("      - name: Publish rolling release")
        assert download_position < merge_position < release_position

        download = publish[download_position:merge_position]
        assert f"name: release-${{{{ github.run_id }}}}-{channel}" in download
        assert "path: release-payload" in download
        assert (
            'python tools/build_release_asset.py merge --existing-url '
            '"https://github.com/gvoze32/fldailyedit/releases/download/latest/catalog.json" '
            "--record release-payload/record.json "
            "--output release-payload/catalog.json"
        ) in publish[merge_position:release_position]
        release = publish[release_position:]
        assert "GH_TOKEN: ${{ github.token }}" in release
        assert "GH_REPO: ${{ github.repository }}" in release
        assert (
            'gh release view latest --repo "$GH_REPO" >/dev/null 2>&1 || '
            'gh release create latest --repo "$GH_REPO" '
            '--title "Latest FL Daily Edit" '
            '--notes "Validated Fast and Deep option files for FL Daily Edit."'
        ) in release
        assert (
            "python tools/publish_release_assets.py \\\n"
            '            --repo "$GH_REPO" \\\n'
            "            --tag latest \\\n"
            f"            release-payload/fldailyedit-fl2026-{channel}.zip \\\n"
            "            release-payload/catalog.json"
        ) in release
        assert "gh release upload" not in release
        assert f"fldailyedit-fl2026-{other_channel}.zip" not in publish
        for line in release.splitlines():
            if "gh release " in line:
                assert '--repo "$GH_REPO"' in line


def test_fast_and_deep_sync_workflows_differ_only_by_channel_and_deep_mode():
    fast = Path(".github/workflows/sync-fast.yml").read_text(encoding="utf-8")
    deep = Path(".github/workflows/sync-deep.yml").read_text(encoding="utf-8")
    release_notes = "Validated Fast and Deep option files for FL Daily Edit."
    normalized_fast = (
        fast.replace(release_notes, "{RELEASE_NOTES}")
        .replace("Fast", "{DISPLAY}")
        .replace("fast", "{channel}")
    )
    normalized_deep = (
        deep.replace(release_notes, "{RELEASE_NOTES}")
        .replace("            --deep \\\n", "")
        .replace("Deep", "{DISPLAY}")
        .replace("deep", "{channel}")
    )

    assert normalized_fast == normalized_deep

def test_readme_lists_every_whitelisted_update_patch_group_and_pair_contract():
    text = README_PATH.read_text(encoding="utf-8")
    contribution_section = text.split("## Player Updates", 1)[1]
    for group in (
        "abilities",
        "position proficiency",
        "playing style",
        "player skills",
        "COM styles",
        "nationality",
        "physical/basic settings",
        "registered position",
    ):
        assert group in contribution_section
    assert "`from`" in contribution_section
    assert "`to`" in contribution_section



def test_installer_build_metadata_discovers_package_and_pins_pyinstaller():
    text = PYPROJECT_PATH.read_text(encoding="utf-8")
    package_find = text.split("[tool.setuptools.packages.find]", 1)[1].split(
        "\n[", 1
    )[0]
    include_line = re.search(r"(?m)^include = \[(.+)\]$", package_find)
    assert include_line is not None
    assert "installer*" in re.findall(r'"([^"]+)"', include_line.group(1))

    dependency_block = text.split("installer-build = [", 1)[1].split("]", 1)[0]
    assert re.findall(r'"([^"]+)"', dependency_block) == ["pyinstaller>=6.14,<7"]


def test_installer_spec_is_one_file_windowed_and_excludes_sensitive_payloads():
    text = INSTALLER_SPEC_PATH.read_text(encoding="utf-8")

    assert "Analysis(" in text
    assert '["installer/__main__.py"]' in text
    assert 'pathex=["."]' in text
    assert "EXE(" in text
    assert 'name="FLDailyEditInstaller"' in text
    assert "console=False" in text
    assert "debug=False" in text
    assert "strip=False" in text
    assert "upx=False" in text
    assert "COLLECT(" not in text

    lowered = text.lower()
    for forbidden in (
        "edit00000000",
        "transfer_summary",
        "credentials",
        "pesxdecrypter",
    ):
        assert forbidden not in lowered


def test_installer_workflow_builds_tests_and_smoke_tests_on_windows():
    text = INSTALLER_WORKFLOW_PATH.read_text(encoding="utf-8")
    build = text.split("\n  publish:\n", 1)[0]

    assert "workflow_dispatch:" in text
    assert "branches: [main]" in text
    for path_filter in (
        "installer/**",
        "tests/test_installer_*.py",
        "tests/test_release_asset.py",
        "FLDailyEditInstaller.spec",
        "pyproject.toml",
        ".github/workflows/build-installer.yml",
        "tools/publish_release_assets.py",
        "tests/test_release_publisher.py",
    ):
        assert path_filter in text

    assert "runs-on: windows-latest" in build
    assert 'python-version: "3.12"' in build
    assert 'python -m pip install -e ".[installer-build]"' in build
    assert 'python -m pip install -e ".[dev]"' in build
    for test_path in (
        "tests/test_installer_catalog.py",
        "tests/test_installer_paths.py",
        "tests/test_installer_install.py",
        "tests/test_installer_app.py",
        "tests/test_release_asset.py",
        "tests/test_workflow_config.py",
    ):
        assert test_path in build
    assert "pyinstaller --clean --noconfirm FLDailyEditInstaller.spec" in build
    assert (
        'Start-Process -FilePath "dist\\FLDailyEditInstaller.exe" '
        '-ArgumentList "--self-test" -Wait -PassThru'
    ) in build
    assert "if ($process.ExitCode -ne 0)" in build
    assert 'throw "Installer self-test exited $($process.ExitCode)"' in build
    assert (
        'Get-FileHash -Path "dist\\FLDailyEditInstaller.exe" -Algorithm SHA256'
        in build
    )
    assert "uses: actions/upload-artifact@v7" in build
    assert "retention-days: 1" in build


def test_installer_publish_job_is_serialized_and_uploads_exact_release_assets():
    text = INSTALLER_WORKFLOW_PATH.read_text(encoding="utf-8")
    publish = "\n  publish:\n" + text.split("\n  publish:\n", 1)[1]

    assert "needs: build" in publish
    assert "runs-on: ubuntu-latest" in publish
    assert "contents: write" in publish
    assert "group: fldailyedit-latest-release" in publish
    assert "cancel-in-progress: false" in publish
    assert "uses: actions/checkout@v7" in publish
    assert "GH_REPO: ${{ github.repository }}" in publish
    assert (
        'gh release view latest --repo "$GH_REPO" >/dev/null 2>&1 || '
        'gh release create latest --repo "$GH_REPO" '
        '--title "Latest FL Daily Edit" '
        '--notes "Validated FL Daily Edit release assets."'
    ) in publish
    assert (
        "python tools/publish_release_assets.py \\\n"
        '            --repo "$GH_REPO" \\\n'
        "            --tag latest \\\n"
        "            release-payload/FLDailyEditInstaller.exe \\\n"
        "            release-payload/FLDailyEditInstaller.exe.sha256"
    ) in publish
    assert "gh release upload" not in publish
    for line in publish.splitlines():
        if "gh release " in line:
            assert '--repo "$GH_REPO"' in line

    actions = re.findall(r"(?m)^\s*uses:\s+([^@\s]+)@", text)
    assert actions
    assert all(action.startswith("actions/") for action in actions)