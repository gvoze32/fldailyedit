"""Immutable README structure and roadmap cleanup contracts."""

import hashlib
import re
from pathlib import Path

import pytest


README_PATHS = (
    Path("README.md"),
    Path("README.id.md"),
    Path("README.es.md"),
    Path("README.pt.md"),
    Path("README.ar.md"),
    Path("README.zh.md"),
    Path("README.it.md"),
    Path("README.ru.md"),
    Path("README.de.md"),
    Path("README.fr.md"),
    Path("README.tr.md"),
)

_LANGUAGE_BADGE_TARGETS = (
    "README.md",
    "README.id.md",
    "README.es.md",
    "README.fr.md",
    "README.pt.md",
    "README.de.md",
    "README.it.md",
    "README.ru.md",
    "README.tr.md",
    "README.ar.md",
    "README.zh.md",
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
        "6ae9b802adb7e7661be45a5d2674540cbfb44610f45feb34b4ab9b67ccf7a1ac",
    ),
    (
        "bash",
        "850b60dfe34ad48f1ba45746854267b56e3972b725453257078f7461b95b4446",
    ),
)

README_STRUCTURE_FIXTURES = {
    Path("README.md"): {
        "heading_counts": (9, 11, 3, 0, 0, 0),
        "badge_targets": _BADGE_TARGETS,
        "fenced_code_blocks": _COMMON_FENCE_FIXTURES,
        "relative_links": (
            "base/EDIT00000000",
            ".github/ISSUE_TEMPLATE/player-update.yml",
            "LICENSE",
        ),
    },
    Path("README.id.md"): {
        "heading_counts": (9, 11, 3, 0, 0, 0),
        "badge_targets": _BADGE_TARGETS,
        "fenced_code_blocks": _COMMON_FENCE_FIXTURES,
        "relative_links": (
            "base/EDIT00000000",
            ".github/ISSUE_TEMPLATE/player-update.yml",
            "LICENSE",
        ),
    },
    Path("README.es.md"): {
        "heading_counts": (9, 11, 3, 0, 0, 0),
        "badge_targets": _BADGE_TARGETS,
        "fenced_code_blocks": _COMMON_FENCE_FIXTURES,
        "relative_links": (
            "base/EDIT00000000",
            ".github/ISSUE_TEMPLATE/player-update.yml",
            "LICENSE",
        ),
    },
    Path("README.pt.md"): {
        "heading_counts": (9, 11, 3, 0, 0, 0),
        "badge_targets": _BADGE_TARGETS,
        "fenced_code_blocks": _COMMON_FENCE_FIXTURES,
        "relative_links": (
            "base/EDIT00000000",
            ".github/ISSUE_TEMPLATE/player-update.yml",
            "LICENSE",
        ),
    },
    Path("README.ar.md"): {
        "heading_counts": (9, 11, 3, 0, 0, 0),
        "badge_targets": _BADGE_TARGETS,
        "fenced_code_blocks": _COMMON_FENCE_FIXTURES,
        "relative_links": (
            "base/EDIT00000000",
            ".github/ISSUE_TEMPLATE/player-update.yml",
            "LICENSE",
        ),
    },
    Path("README.zh.md"): {
        "heading_counts": (9, 11, 3, 0, 0, 0),
        "badge_targets": _BADGE_TARGETS,
        "fenced_code_blocks": _COMMON_FENCE_FIXTURES,
        "relative_links": (
            "base/EDIT00000000",
            ".github/ISSUE_TEMPLATE/player-update.yml",
            "LICENSE",
        ),
    },
    Path("README.it.md"): {
        "heading_counts": (9, 11, 3, 0, 0, 0),
        "badge_targets": _BADGE_TARGETS,
        "fenced_code_blocks": _COMMON_FENCE_FIXTURES,
        "relative_links": (
            "base/EDIT00000000",
            ".github/ISSUE_TEMPLATE/player-update.yml",
            "LICENSE",
        ),
    },
    Path("README.ru.md"): {
        "heading_counts": (9, 11, 3, 0, 0, 0),
        "badge_targets": _BADGE_TARGETS,
        "fenced_code_blocks": _COMMON_FENCE_FIXTURES,
        "relative_links": (
            "base/EDIT00000000",
            ".github/ISSUE_TEMPLATE/player-update.yml",
            "LICENSE",
        ),
    },
    Path("README.de.md"): {
        "heading_counts": (9, 11, 3, 0, 0, 0),
        "badge_targets": _BADGE_TARGETS,
        "fenced_code_blocks": (
            _COMMON_FENCE_FIXTURES[0],
            (
                "bash",
                "0246034e1e3ae6f9d18de5f4fb696dd04e991a75fde50c4ca9294e559bac0250",
            ),
            _COMMON_FENCE_FIXTURES[2],
        ),
        "relative_links": (
            "base/EDIT00000000",
            ".github/ISSUE_TEMPLATE/player-update.yml",
            "LICENSE",
        ),
    },
    Path("README.fr.md"): {
        "heading_counts": (9, 11, 3, 0, 0, 0),
        "badge_targets": _BADGE_TARGETS,
        "fenced_code_blocks": (
            _COMMON_FENCE_FIXTURES[0],
            (
                "bash",
                "9ad788f9bd22ec1478c92c412796236837e473e54decbfa699e0cd7e642dbb66",
            ),
            _COMMON_FENCE_FIXTURES[2],
        ),
        "relative_links": (
            "base/EDIT00000000",
            ".github/ISSUE_TEMPLATE/player-update.yml",
            "LICENSE",
        ),
    },
    Path("README.tr.md"): {
        "heading_counts": (9, 11, 3, 0, 0, 0),
        "badge_targets": _BADGE_TARGETS,
        "fenced_code_blocks": (
            _COMMON_FENCE_FIXTURES[0],
            (
                "bash",
                "fae93c55a19e0a3ccee28ce2d9020e9fabbf03a93388728b5fbcc430024cb4b1",
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
    Path("README.md"): (
        "## Roadmap / Work in progress",
        "Local Update",
        "multi-base",
    ),
    Path("README.id.md"): (
        "## Roadmap / Sedang dikerjakan",
        "Pembaruan Lokal",
        "multi-base",
    ),
    Path("README.es.md"): (
        "## Hoja de ruta / Trabajo en curso",
        "Actualización local",
        "multi-bases",
    ),
    Path("README.pt.md"): (
        "## Roteiro / Trabalho em andamento",
        "Atualização Local",
        "múltiplas bases",
    ),
    Path("README.ar.md"): (
        "## خارطة الطريق / قيد التطوير",
        "التحديث المحلي",
        "القواعد المتعددة",
    ),
    Path("README.zh.md"): (
        "## 路线图 / 进行中的工作",
        "本地更新模式",
        "多个预构建基础文件",
    ),
    Path("README.it.md"): (
        "## Roadmap / Lavori in corso",
        "Aggiornamento locale",
        "più basi",
    ),
    Path("README.ru.md"): (
        "## План развития / В работе",
        "Локальное обновление",
        "отдельных баз",
    ),
    Path("README.de.md"): (
        "## Roadmap / In Arbeit",
        "Lokales Update",
        "Multi-Base-Distribution",
    ),
    Path("README.fr.md"): (
        "## Feuille de route / En cours de développement",
        "Mise à jour locale",
        "bases multiples",
    ),
    Path("README.tr.md"): (
        "## Yol Haritası / Devam Eden Çalışmalar",
        "Yerel Güncelleme",
        "çoklu temel dosya",
    ),
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


def _badge_targets(text: str) -> tuple[str, ...]:
    return tuple(_BADGE_RE.findall(text))


def _relative_links(text: str) -> tuple[str, ...]:
    links: list[str] = []
    for target in _LINK_RE.findall(text):
        target = target.split(maxsplit=1)[0]
        if re.match(r"(?i)^[a-z][a-z0-9+.-]*:", target):
            continue
        if target.startswith(("#", "/")):
            continue
        links.append(target)
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
    roadmap_heading, local_update, multi_base = ROADMAP_FIXTURES[path]

    assert _heading_counts(text) == structure["heading_counts"]
    assert _badge_targets(text) == structure["badge_targets"]
    assert _fenced_code_blocks(text) == structure["fenced_code_blocks"]
    assert _relative_links(text) == structure["relative_links"]

    roadmap = _roadmap_section(text, roadmap_heading)
    planned_items = _PLANNED_ITEM_RE.findall(roadmap)
    if path == Path("README.md"):
        assert len(planned_items) == 2
        delivered, future = planned_items
        assert local_update in delivered
        assert "Fast" in roadmap
        assert "Deep" in roadmap
        assert "in-place backup" in roadmap
        assert "atomically" in roadmap
        assert "future" in future.casefold()
        assert "PES 2021" in roadmap
        assert "UML" in roadmap
    else:
        assert len(planned_items) == 1
        assert local_update in planned_items[0]
        assert multi_base in planned_items[0]
    assert not ("ovr" in roadmap.casefold() and "pes retro" in roadmap.casefold())
