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
        "the four-step wizard updates",
        "validated common-layout",
        "Local eligibility is independent of the save’s SPFL/PES/UML label",
        "downloadable releases remain validated FL26/SPFL targets",
    ),
    Path("README.id.md"): (
        "## Roadmap / Sedang dikerjakan",
        "Pembaruan Lokal",
        "wizard empat langkah kini memperbarui",
        "ber-layout umum tervalidasi",
        "Kelayakan lokal tidak bergantung pada label SPFL/PES/UML",
        "rilis yang dapat diunduh tetap hanya menargetkan FL26/SPFL tervalidasi",
    ),
    Path("README.es.md"): (
        "## Hoja de ruta / Trabajo en curso",
        "Actualización local",
        "el asistente de cuatro pasos ya actualiza",
        "diseño común validado",
        "La elegibilidad local es independiente de la etiqueta SPFL/PES/UML",
        "las versiones descargables siguen limitadas a objetivos FL26/SPFL validados",
    ),
    Path("README.pt.md"): (
        "## Roteiro / Trabalho em andamento",
        "Atualização Local",
        "o assistente de quatro etapas agora atualiza",
        "layout comum validado",
        "A elegibilidade local independe do rótulo SPFL/PES/UML",
        "as versões para download continuam restritas a alvos FL26/SPFL validados",
    ),
    Path("README.ar.md"): (
        "## خارطة الطريق / قيد التطوير",
        "التحديث المحلي",
        "يحدّث المعالج المكوّن من أربع خطوات الآن",
        "ذي التخطيط المشترك المتحقق منه",
        "لا تعتمد أهلية التحديث المحلي على تسمية SPFL/PES/UML",
        "تظل الإصدارات القابلة للتنزيل مقتصرة على أهداف FL26/SPFL المتحقق منها",
    ),
    Path("README.zh.md"): (
        "## 路线图 / 进行中的工作",
        "本地更新模式",
        "四步向导现已可",
        "采用已验证通用布局",
        "本地更新资格不受存档的 SPFL/PES/UML 标签影响",
        "可下载版本仍仅面向已验证的 FL26/SPFL 目标",
    ),
    Path("README.it.md"): (
        "## Roadmap / Lavori in corso",
        "Aggiornamento locale",
        "la procedura guidata in quattro passaggi ora aggiorna",
        "layout comune convalidato",
        "L'idoneità locale è indipendente dall'etichetta SPFL/PES/UML",
        "le versioni scaricabili restano limitate a target FL26/SPFL convalidati",
    ),
    Path("README.ru.md"): (
        "## План развития / В работе",
        "Локальное обновление",
        "четырёхшаговый мастер теперь обновляет",
        "проверенной общей структурой",
        "Допуск к локальному обновлению не зависит от метки SPFL/PES/UML",
        "загружаемые выпуски по-прежнему предназначены только для проверенных целей FL26/SPFL",
    ),
    Path("README.de.md"): (
        "## Roadmap / In Arbeit",
        "Lokales Update",
        "der vierstufige Assistent aktualisiert jetzt",
        "validiertem Standardlayout",
        "Die lokale Eignung ist unabhängig von der SPFL/PES/UML-Kennzeichnung",
        "herunterladbare Releases bleiben auf validierte FL26/SPFL-Ziele beschränkt",
    ),
    Path("README.fr.md"): (
        "## Feuille de route / En cours de développement",
        "Mise à jour locale",
        "l'assistant en quatre étapes met désormais à jour",
        "structure commune validée",
        "L'éligibilité locale est indépendante de l'étiquette SPFL/PES/UML",
        "les versions téléchargeables restent limitées aux cibles FL26/SPFL validées",
    ),
    Path("README.tr.md"): (
        "## Yol Haritası / Devam Eden Çalışmalar",
        "Yerel Güncelleme",
        "dört adımlı sihirbaz artık",
        "doğrulanmış ortak düzene",
        "Yerel uygunluk, kaydın SPFL/PES/UML etiketinden bağımsızdır",
        "indirilebilir sürümler yalnızca doğrulanmış FL26/SPFL hedefleriyle sınırlı kalır",
    ),
}

_LOCAL_MUTATION_FUTURE_MARKERS = {
    Path("README.md"): ("future", "will be added"),
    Path("README.id.md"): (
        "direncanakan",
        "akan ditambahkan",
        "sedang dalam proses",
    ),
    Path("README.es.md"): (
        "planificado",
        "se añadirá",
        "se añadira",
    ),
    Path("README.pt.md"): (
        "planejado",
        "será adicionado",
        "sera adicionado",
    ),
    Path("README.ar.md"): (
        "مخطط له",
        "ستتم إضافة",
        "سيتم إضافة",
    ),
    Path("README.zh.md"): ("已规划", "正在开发", "将"),
    Path("README.it.md"): (
        "pianificato",
        "verrà aggiunta",
        "verra aggiunta",
    ),
    Path("README.ru.md"): (
        "запланирована",
        "в разработке",
        "будет добавлен",
    ),
    Path("README.de.md"): (
        "geplant",
        "wird ein lokaler update-modus",
    ),
    Path("README.fr.md"): (
        "planifié",
        "sera ajouté",
        "sera ajoute",
    ),
    Path("README.tr.md"): (
        "planlanan",
        "geliştirilmekte",
        "eklenecektir",
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
    (
        roadmap_heading,
        local_update,
        delivered_update,
        validated_layout,
        label_independence,
        remote_fl26_only,
    ) = ROADMAP_FIXTURES[path]

    assert _heading_counts(text) == structure["heading_counts"]
    assert _badge_targets(text) == structure["badge_targets"]
    assert _fenced_code_blocks(text) == structure["fenced_code_blocks"]
    assert _relative_links(text) == structure["relative_links"]

    roadmap = _roadmap_section(text, roadmap_heading)
    normalized_roadmap = " ".join(roadmap.split())
    compact_roadmap = re.sub(r"\s+", "", roadmap)
    planned_items = _PLANNED_ITEM_RE.findall(roadmap)
    assert len(planned_items) == 1
    assert local_update in planned_items[0]
    for required in (
        delivered_update,
        validated_layout,
        label_independence,
        remote_fl26_only,
        "`EDIT00000000`",
        "SPFL/PES/UML",
    ):
        assert re.sub(r"\s+", "", required) in compact_roadmap

    assert "Future: Multi-target local updates" not in normalized_roadmap
    assert not re.search(
        r"(?is)(?:future|will be added).{0,200}local|"
        r"local.{0,200}(?:future|will be added)",
        normalized_roadmap,
    )
    for forbidden_marker in _LOCAL_MUTATION_FUTURE_MARKERS[path]:
        assert forbidden_marker.casefold() not in normalized_roadmap.casefold()
    if path == Path("README.md"):
        assert "common-layout" in normalized_roadmap or "standard" in normalized_roadmap.casefold()
        assert "Fast" in normalized_roadmap
        assert "Deep" in normalized_roadmap
        assert "in-place backup" in normalized_roadmap
        assert "atomically" in normalized_roadmap
        assert "remote" in normalized_roadmap.casefold()
        assert "FL26" in normalized_roadmap

    assert not ("ovr" in normalized_roadmap.casefold() and "pes retro" in normalized_roadmap.casefold())
