"""
OVR Calculator — estimasi overall rating PES 2021 untuk player apapun.

Usage:
    PYTHONPATH=. python3 tools/ovr_calc.py <player_id>
    PYTHONPATH=. python3 tools/ovr_calc.py <player_id> --spec players/<file>.json
    PYTHONPATH=. python3 tools/ovr_calc.py <player_id> --position RB
    PYTHONPATH=. python3 tools/ovr_calc.py <player_id> --spec players/<file>.json --position RB

Contoh:
    PYTHONPATH=. python3 tools/ovr_calc.py 162196
    PYTHONPATH=. python3 tools/ovr_calc.py 162196 --spec players/marco-palestra.json
    PYTHONPATH=. python3 tools/ovr_calc.py 200000 --spec players/dastan-satpaev.json --position CF
"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path

from editor import crypto
from editor.editfile import EditFile
from editor.player_codec import ABILITY_FIELDS, POSITION_NAMES

# ---------------------------------------------------------------------------
# Formula OVR PES 2021 — bobot per atribut per posisi
# Berdasarkan reverse-engineering komunitas (pesmaster, pes-stats).
# Nilai bobot adalah relatif; dinormalisasi otomatis saat kalkulasi.
# ---------------------------------------------------------------------------
#
# Urutan posisi sesuai POSITION_NAMES:
#  0=GK  1=CB  2=LB  3=RB  4=DMF  5=CMF  6=LMF  7=RMF
#  8=AMF  9=LWF  10=RWF  11=SS  12=CF

_POS_IDX = {pos: i for i, pos in enumerate(POSITION_NAMES)}

_WEIGHTS: dict[str, list[float]] = {
    #                          GK    CB    LB    RB    DMF   CMF   LMF   RMF   AMF   LWF   RWF   SS    CF
    "attacking_awareness":   [0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.10, 0.10, 0.10, 0.12, 0.15],
    "ball_control":          [0.03, 0.08, 0.08, 0.08, 0.10, 0.15, 0.12, 0.12, 0.15, 0.14, 0.14, 0.14, 0.10],
    "dribbling":             [0.03, 0.04, 0.08, 0.08, 0.05, 0.10, 0.12, 0.12, 0.14, 0.18, 0.18, 0.14, 0.10],
    "tight_possession":      [0.03, 0.04, 0.04, 0.04, 0.05, 0.08, 0.08, 0.08, 0.10, 0.08, 0.08, 0.08, 0.05],
    "low_pass":              [0.03, 0.05, 0.10, 0.10, 0.10, 0.14, 0.12, 0.12, 0.14, 0.08, 0.08, 0.10, 0.05],
    "lofted_pass":           [0.03, 0.05, 0.10, 0.10, 0.05, 0.05, 0.08, 0.08, 0.05, 0.05, 0.05, 0.05, 0.05],
    "finishing":             [0.03, 0.03, 0.03, 0.03, 0.03, 0.04, 0.04, 0.04, 0.10, 0.14, 0.14, 0.18, 0.25],
    "heading":               [0.03, 0.10, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.06, 0.10],
    "place_kicking":         [0.03, 0.03, 0.03, 0.03, 0.04, 0.04, 0.04, 0.04, 0.04, 0.04, 0.04, 0.04, 0.04],
    "curl":                  [0.03, 0.03, 0.04, 0.04, 0.04, 0.04, 0.04, 0.04, 0.05, 0.05, 0.05, 0.05, 0.05],
    "speed":                 [0.03, 0.05, 0.10, 0.10, 0.05, 0.05, 0.10, 0.10, 0.10, 0.15, 0.15, 0.10, 0.10],
    "acceleration":          [0.03, 0.05, 0.10, 0.10, 0.05, 0.05, 0.10, 0.10, 0.10, 0.15, 0.15, 0.10, 0.10],
    "kicking_power":         [0.03, 0.04, 0.04, 0.04, 0.04, 0.04, 0.04, 0.04, 0.04, 0.04, 0.04, 0.04, 0.05],
    "jump":                  [0.03, 0.10, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.04, 0.04, 0.04, 0.04, 0.06],
    "physical_contact":      [0.03, 0.10, 0.10, 0.10, 0.10, 0.05, 0.05, 0.05, 0.04, 0.04, 0.04, 0.04, 0.04],
    "balance":               [0.03, 0.04, 0.04, 0.04, 0.04, 0.04, 0.04, 0.04, 0.04, 0.04, 0.04, 0.04, 0.04],
    "stamina":               [0.03, 0.05, 0.10, 0.10, 0.10, 0.10, 0.10, 0.10, 0.08, 0.08, 0.08, 0.08, 0.06],
    "defensive_awareness":   [0.05, 0.20, 0.16, 0.16, 0.16, 0.10, 0.10, 0.10, 0.05, 0.04, 0.04, 0.04, 0.03],
    "ball_winning":          [0.03, 0.15, 0.10, 0.10, 0.15, 0.10, 0.05, 0.05, 0.04, 0.04, 0.04, 0.04, 0.03],
    "aggression":            [0.03, 0.05, 0.05, 0.05, 0.06, 0.05, 0.05, 0.05, 0.04, 0.04, 0.04, 0.04, 0.04],
    "gk_awareness":          [0.32, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00],
    "catching":              [0.15, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00],
    "clearing":              [0.05, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00],
    "reflexes":              [0.15, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00],
    "gk_reach":              [0.10, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00],
}


def calc_ovr(abilities: dict[str, int], position: str) -> float:
    """Estimasi weighted-average OVR untuk posisi tertentu. Bobot dinormalisasi otomatis."""
    idx = _POS_IDX.get(position.upper())
    if idx is None:
        raise ValueError(f"Unknown position: {position!r}. Valid: {list(POSITION_NAMES)}")
    total_w = sum(_WEIGHTS[a][idx] for a in ABILITY_FIELDS)
    if total_w == 0:
        return 0.0
    score = sum(_WEIGHTS[a][idx] * abilities.get(a, 40) for a in ABILITY_FIELDS)
    return score / total_w


def _apply_spec_patches(
    abilities: dict[str, int],
    spec_path: Path,
) -> dict[str, int]:
    """Apply 'to' values dari player spec JSON ke abilities dict."""
    raw = json.loads(spec_path.read_text(encoding="utf-8"))
    pes = raw.get("pes", {})
    ab_patches = pes.get("abilities", {})
    result = dict(abilities)
    for field, patch in ab_patches.items():
        if isinstance(patch, dict) and "to" in patch:
            result[field] = int(patch["to"])
    return result


def _load_base_abilities(player_id: int) -> tuple[dict[str, int], str]:
    """Decrypt base, load edit file, return (abilities_dict, registered_position)."""
    source = Path("base/EDIT00000000")
    if not source.exists():
        raise FileNotFoundError(
            "base/EDIT00000000 not found. Run from project root."
        )
    tmp = Path(tempfile.mkdtemp(prefix="ovr_calc_"))
    try:
        src_copy = tmp / "EDIT00000000"
        shutil.copy2(source, src_copy)
        decrypted = crypto.decrypt(src_copy)
        try:
            edit_file = EditFile()
            edit_file.load(decrypted / "data.dat")
            profile = edit_file.get_player_ability_profile(player_id)
            if profile is None:
                raise ValueError(f"Player ID {player_id} not found in base file")
            return dict(profile.abilities), profile.registered_position
        finally:
            crypto.cleanup_temp(decrypted)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _print_ovr_table(abilities: dict[str, int], highlight: str | None = None) -> None:
    """Print OVR untuk semua posisi."""
    print(f"\n  {'Posisi':<8} {'OVR Est.':>9}")
    print(f"  {'-'*20}")
    for pos in POSITION_NAMES:
        ovr = calc_ovr(abilities, pos)
        marker = " ◄" if pos == highlight else ""
        print(f"  {pos:<8} {ovr:>8.1f}{marker}")


def _print_full_report(
    base_abs: dict[str, int],
    after_abs: dict[str, int] | None,
    base_pos: str,
    spec_path: Path | None,
    highlight_pos: str | None,
) -> None:
    has_spec = after_abs is not None and after_abs != base_abs

    # Header
    print(f"\n{'='*62}")
    label = f"Player base @ {base_pos}"
    if spec_path:
        label += f" + {spec_path.name}"
    print(f"  {label}")
    print(f"{'='*62}")

    # Ability comparison table
    print(f"\n  {'Atribut':<25} {'Base':>6}", end="")
    if has_spec:
        print(f" {'After':>6} {'Gap':>6}", end="")
    print()
    print(f"  {'-'*55}")

    for attr in ABILITY_FIELDS:
        bv = base_abs.get(attr, 40)
        line = f"  {attr:<25} {bv:>6}"
        if has_spec:
            av = after_abs.get(attr, 40)  # type: ignore[union-attr]
            gap = av - bv
            marker = " ◄" if gap != 0 else ""
            line += f" {av:>6} {gap:>+6}{marker}"
        print(line)

    # OVR table
    print(f"\n{'='*62}")
    print(f"  OVR estimasi semua posisi (bobot per posisi berbeda):")
    print(f"{'='*62}")

    hl = (highlight_pos or base_pos).upper()
    if has_spec:
        print(f"\n  {'Posisi':<8} {'Base OVR':>10} {'After OVR':>10} {'Δ':>6}")
        print(f"  {'-'*38}")
        for pos in POSITION_NAMES:
            b_ovr = calc_ovr(base_abs, pos)
            a_ovr = calc_ovr(after_abs, pos)  # type: ignore[arg-type]
            delta = a_ovr - b_ovr
            marker = " ◄" if pos == hl else ""
            print(f"  {pos:<8} {b_ovr:>10.1f} {a_ovr:>10.1f} {delta:>+6.1f}{marker}")
    else:
        _print_ovr_table(base_abs, highlight=hl)

    # Changed attributes summary
    if has_spec:
        changed = [
            (a, base_abs.get(a, 40), after_abs.get(a, 40))  # type: ignore[union-attr]
            for a in ABILITY_FIELDS
            if base_abs.get(a, 40) != after_abs.get(a, 40)  # type: ignore[union-attr]
        ]
        if changed:
            print(f"\n  {len(changed)} atribut berubah:")
            for attr, old, new in changed:
                print(f"    {attr:<25}  {old:>3} → {new:>3}  ({new - old:+d})")

    print(f"\n{'='*62}")
    print("  Catatan: formula OVR adalah estimasi berdasarkan bobot")
    print("  komunitas, bukan formula resmi Konami.")
    print(f"{'='*62}\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Estimasi OVR PES 2021 untuk player dari base file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("player_id", type=int, help="PES player ID (misal: 162196)")
    parser.add_argument(
        "--spec",
        type=Path,
        metavar="FILE",
        help="Path ke player spec JSON (misal: players/marco-palestra.json)",
    )
    parser.add_argument(
        "--position",
        metavar="POS",
        help=f"Sorot posisi tertentu di output. Valid: {', '.join(POSITION_NAMES)}",
    )
    args = parser.parse_args()

    try:
        base_abs, base_pos = _load_base_abilities(args.player_id)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return

    after_abs: dict[str, int] | None = None
    if args.spec:
        if not args.spec.exists():
            print(f"ERROR: spec file not found: {args.spec}")
            return
        try:
            after_abs = _apply_spec_patches(base_abs, args.spec)
        except Exception as exc:
            print(f"ERROR reading spec: {exc}")
            return

    _print_full_report(
        base_abs=base_abs,
        after_abs=after_abs,
        base_pos=base_pos,
        spec_path=args.spec,
        highlight_pos=args.position,
    )


if __name__ == "__main__":
    main()
