"""Immutable README structure and roadmap cleanup contracts."""

import hashlib
import re
from pathlib import Path

import pytest


README_PATHS = (
    Path("README.md"),
    Path("docs/readmes/README.id.md"),
    Path("docs/readmes/README.es.md"),
    Path("docs/readmes/README.pt.md"),
    Path("docs/readmes/README.ar.md"),
    Path("docs/readmes/README.zh.md"),
    Path("docs/readmes/README.it.md"),
    Path("docs/readmes/README.ru.md"),
    Path("docs/readmes/README.de.md"),
    Path("docs/readmes/README.fr.md"),
    Path("docs/readmes/README.tr.md"),
)

_LANGUAGE_BADGE_TARGETS = (
    "README.md",
    "docs/readmes/README.id.md",
    "docs/readmes/README.es.md",
    "docs/readmes/README.fr.md",
    "docs/readmes/README.pt.md",
    "docs/readmes/README.de.md",
    "docs/readmes/README.it.md",
    "docs/readmes/README.ru.md",
    "docs/readmes/README.tr.md",
    "docs/readmes/README.ar.md",
    "docs/readmes/README.zh.md",
)
_BADGE_TARGETS = _LANGUAGE_BADGE_TARGETS + (
    "https://www.python.org/",
    "LICENSE",
)
_COMMON_FENCE_FIXTURES = (
    (
        "bash",
        "af9d25bac82d1a2e1527839f6dd757241c9525397e52097cec18769a534fe0f2",
    ),
    (
        "bash",
        "4c3d49affa7ed46ba77326624356642faee14a6cfd2ef084f271d187cb8c4565",
    ),
    (
        "bash",
        "850b60dfe34ad48f1ba45746854267b56e3972b725453257078f7461b95b4446",
    ),
)
_README_COMMAND_FENCE_FIXTURES = (
    _COMMON_FENCE_FIXTURES[0],
    (
        "bash",
        "4c3d49affa7ed46ba77326624356642faee14a6cfd2ef084f271d187cb8c4565",
    ),
    _COMMON_FENCE_FIXTURES[2],
)

_INDONESIAN_COMMAND_FENCE_FIXTURES = (
    _COMMON_FENCE_FIXTURES[0],
    (
        "bash",
        "4c3d49affa7ed46ba77326624356642faee14a6cfd2ef084f271d187cb8c4565",
    ),
    _COMMON_FENCE_FIXTURES[2],
)

README_STRUCTURE_FIXTURES = {
    Path("README.md"): {
        "heading_counts": (9, 9, 0, 0, 0, 0),
        "badge_targets": _BADGE_TARGETS,
        "fenced_code_blocks": _README_COMMAND_FENCE_FIXTURES,
        "relative_links": (
            "base/EDIT00000000",
            ".github/ISSUE_TEMPLATE/player-update.yml",
            "LICENSE",
        ),
    },
    Path("docs/readmes/README.id.md"): {
        "heading_counts": (9, 9, 0, 0, 0, 0),
        "badge_targets": _BADGE_TARGETS,
        "fenced_code_blocks": _INDONESIAN_COMMAND_FENCE_FIXTURES,
        "relative_links": (
            "base/EDIT00000000",
            ".github/ISSUE_TEMPLATE/player-update.yml",
            "LICENSE",
        ),
    },
    Path("docs/readmes/README.es.md"): {
        "heading_counts": (9, 9, 0, 0, 0, 0),
        "badge_targets": _BADGE_TARGETS,
        "fenced_code_blocks": _COMMON_FENCE_FIXTURES,
        "relative_links": (
            "base/EDIT00000000",
            ".github/ISSUE_TEMPLATE/player-update.yml",
            "LICENSE",
        ),
    },
    Path("docs/readmes/README.pt.md"): {
        "heading_counts": (9, 9, 0, 0, 0, 0),
        "badge_targets": _BADGE_TARGETS,
        "fenced_code_blocks": _COMMON_FENCE_FIXTURES,
        "relative_links": (
            "base/EDIT00000000",
            ".github/ISSUE_TEMPLATE/player-update.yml",
            "LICENSE",
        ),
    },
    Path("docs/readmes/README.ar.md"): {
        "heading_counts": (9, 9, 0, 0, 0, 0),
        "badge_targets": _BADGE_TARGETS,
        "fenced_code_blocks": _COMMON_FENCE_FIXTURES,
        "relative_links": (
            "base/EDIT00000000",
            ".github/ISSUE_TEMPLATE/player-update.yml",
            "LICENSE",
        ),
    },
    Path("docs/readmes/README.zh.md"): {
        "heading_counts": (9, 9, 0, 0, 0, 0),
        "badge_targets": _BADGE_TARGETS,
        "fenced_code_blocks": _COMMON_FENCE_FIXTURES,
        "relative_links": (
            "base/EDIT00000000",
            ".github/ISSUE_TEMPLATE/player-update.yml",
            "LICENSE",
        ),
    },
    Path("docs/readmes/README.it.md"): {
        "heading_counts": (9, 9, 0, 0, 0, 0),
        "badge_targets": _BADGE_TARGETS,
        "fenced_code_blocks": _COMMON_FENCE_FIXTURES,
        "relative_links": (
            "base/EDIT00000000",
            ".github/ISSUE_TEMPLATE/player-update.yml",
            "LICENSE",
        ),
    },
    Path("docs/readmes/README.ru.md"): {
        "heading_counts": (9, 9, 0, 0, 0, 0),
        "badge_targets": _BADGE_TARGETS,
        "fenced_code_blocks": _COMMON_FENCE_FIXTURES,
        "relative_links": (
            "base/EDIT00000000",
            ".github/ISSUE_TEMPLATE/player-update.yml",
            "LICENSE",
        ),
    },
    Path("docs/readmes/README.de.md"): {
        "heading_counts": (9, 9, 0, 0, 0, 0),
        "badge_targets": _BADGE_TARGETS,
        "fenced_code_blocks": (
            _COMMON_FENCE_FIXTURES[0],
            (
                "bash",
                "4c3d49affa7ed46ba77326624356642faee14a6cfd2ef084f271d187cb8c4565",
            ),
            _COMMON_FENCE_FIXTURES[2],
        ),
        "relative_links": (
            "base/EDIT00000000",
            ".github/ISSUE_TEMPLATE/player-update.yml",
            "LICENSE",
        ),
    },
    Path("docs/readmes/README.fr.md"): {
        "heading_counts": (9, 9, 0, 0, 0, 0),
        "badge_targets": _BADGE_TARGETS,
        "fenced_code_blocks": (
            _COMMON_FENCE_FIXTURES[0],
            (
                "bash",
                "4c3d49affa7ed46ba77326624356642faee14a6cfd2ef084f271d187cb8c4565",
            ),
            _COMMON_FENCE_FIXTURES[2],
        ),
        "relative_links": (
            "base/EDIT00000000",
            ".github/ISSUE_TEMPLATE/player-update.yml",
            "LICENSE",
        ),
    },
    Path("docs/readmes/README.tr.md"): {
        "heading_counts": (9, 9, 0, 0, 0, 0),
        "badge_targets": _BADGE_TARGETS,
        "fenced_code_blocks": (
            _COMMON_FENCE_FIXTURES[0],
            (
                "bash",
                "4c3d49affa7ed46ba77326624356642faee14a6cfd2ef084f271d187cb8c4565",
            ),
            _COMMON_FENCE_FIXTURES[2],
        ),
        "relative_links": (
            "base/EDIT00000000",
            ".github/ISSUE_TEMPLATE/player-update.yml",
            "LICENSE",
        ),
    },
}

ROADMAP_FIXTURES = {
    Path("README.md"): None,
    Path("docs/readmes/README.id.md"): None,
    Path("docs/readmes/README.es.md"): None,
    Path("docs/readmes/README.pt.md"): None,
    Path("docs/readmes/README.ar.md"): None,
    Path("docs/readmes/README.zh.md"): None,
    Path("docs/readmes/README.it.md"): None,
    Path("docs/readmes/README.ru.md"): None,
    Path("docs/readmes/README.de.md"): None,
    Path("docs/readmes/README.fr.md"): None,
    Path("docs/readmes/README.tr.md"): None,
}




_HEADING_RE = re.compile(r"(?m)^(#{1,6})\s+.+$")
_BADGE_RE = re.compile(r"\[!\[[^\]]+\]\([^)]*\)\]\(([^)]+)\)")
_LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
_FENCE_RE = re.compile(r"(?ms)^```([^\n]*)\n.*?^```$")
_PLANNED_ITEM_RE = re.compile(r"(?m)^(?:\d+[.)]|[-*+])\s+.+$")


def _heading_counts(text: str) -> tuple[int, ...]:
    return tuple(
        sum(len(match.group(1)) == level for match in _HEADING_RE.finditer(text))
        for level in range(1, 7)
    )


def _normalize_relative_target(path: Path, target: str) -> str:
    if re.match(r"(?i)^[a-z][a-z0-9+.-]*:", target):
        return target
    return (path.parent / target).resolve().relative_to(Path.cwd()).as_posix()

def _badge_targets(text: str, path: Path) -> tuple[str, ...]:
    return tuple(
        _normalize_relative_target(path, target)
        for target in _BADGE_RE.findall(text)
    )


def _relative_links(path: Path, text: str) -> tuple[str, ...]:
    links: list[str] = []
    for target in _LINK_RE.findall(text):
        target = target.split(maxsplit=1)[0]
        if re.match(r"(?i)^[a-z][a-z0-9+.-]*:", target):
            continue
        if target.startswith(("#", "/")):
            continue
        links.append(_normalize_relative_target(path, target))
    return tuple(links)


def _fenced_code_blocks(text: str) -> tuple[tuple[str, str], ...]:
    return tuple(
        (
            match.group(1),
            hashlib.sha256(match.group(0).encode("utf-8")).hexdigest(),
        )
        for match in _FENCE_RE.finditer(text)
    )


def _roadmap_section(text: str, heading: str) -> str:
    heading_match = re.search(
        rf"(?m)^{re.escape(heading)}\s*$",
        text,
    )
    assert heading_match is not None, heading
    section_start = heading_match.end()
    next_heading = re.search(r"(?m)^##\s+", text[section_start:])
    section_end = (
        section_start + next_heading.start() if next_heading else len(text)
    )
    return text[section_start:section_end]


@pytest.mark.parametrize("path", README_PATHS)
def test_readmes_preserve_immutable_structure_and_clean_roadmap(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    structure = README_STRUCTURE_FIXTURES[path]
    roadmap_fixture = ROADMAP_FIXTURES[path]

    assert _heading_counts(text) == structure["heading_counts"]
    assert _badge_targets(text, path) == structure["badge_targets"]
    assert _fenced_code_blocks(text) == structure["fenced_code_blocks"]
    assert _relative_links(path, text) == structure["relative_links"]

    if roadmap_fixture is None:
        return

    roadmap_heading, completion_marker = roadmap_fixture
    roadmap = _roadmap_section(text, roadmap_heading)
    normalized_roadmap = " ".join(roadmap.split())
    planned_items = _PLANNED_ITEM_RE.findall(roadmap)

    assert planned_items == []
    assert completion_marker in normalized_roadmap
    assert "`EDIT00000000`" not in roadmap
    assert "SPFL/PES/UML" not in roadmap
    assert "Local Update" not in normalized_roadmap

    assert not ("ovr" in normalized_roadmap.casefold() and "pes retro" in normalized_roadmap.casefold())
