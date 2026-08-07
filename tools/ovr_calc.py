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
    PYTHONPATH=. python3 tools/ovr_calc.py 1073003 --spec players/dastan-satpaev.json --position CF
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import cast

from editor import crypto
from editor.editfile import EditFile
from editor.player_codec import ABILITY_FIELDS, POSITION_NAMES
from editor.player_ovr import PlayerOvrError, calculate_ovr_tenths


def _format_ovr(value_tenths: int) -> str:
    return f"{value_tenths // 10}.{value_tenths % 10}"


def _apply_spec_patches(
    abilities: dict[str, int],
    spec_path: Path,
) -> dict[str, object]:
    """Apply raw 'to' values from a player spec to an ability map."""
    raw = json.loads(spec_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise PlayerOvrError("OVR spec must be an object")
    pes = raw.get("pes", {})
    if not isinstance(pes, dict):
        raise PlayerOvrError("OVR spec pes must be an object")
    ab_patches = pes.get("abilities", {})
    if not isinstance(ab_patches, dict):
        raise PlayerOvrError("OVR spec abilities must be an object")

    result: dict[str, object] = dict(abilities)
    for field, patch in ab_patches.items():
        if field not in ABILITY_FIELDS:
            raise PlayerOvrError(f"unsupported OVR ability patch: {field!r}")
        if not isinstance(patch, dict) or "to" not in patch:
            raise PlayerOvrError(
                f"OVR ability patch {field!r} must contain a 'to' value"
            )
        result[field] = patch["to"]
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
        ovr = calculate_ovr_tenths(abilities, pos)
        marker = " ◄" if pos == highlight else ""
        print(f"  {pos:<8} {_format_ovr(ovr):>8}{marker}")


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
        bv = base_abs[attr]
        line = f"  {attr:<25} {bv:>6}"
        if has_spec:
            av = after_abs[attr]  # type: ignore[index]
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
            base_ovr = calculate_ovr_tenths(base_abs, pos)
            after_ovr = calculate_ovr_tenths(after_abs, pos)  # type: ignore[arg-type]
            delta = after_ovr - base_ovr
            delta_text = f"{'+' if delta >= 0 else '-'}{_format_ovr(abs(delta))}"
            marker = " ◄" if pos == hl else ""
            print(
                f"  {pos:<8} {_format_ovr(base_ovr):>10} "
                f"{_format_ovr(after_ovr):>10} {delta_text:>6}{marker}"
            )
    else:
        _print_ovr_table(base_abs, highlight=hl)

    # Changed attributes summary
    if has_spec:
        changed = [
            (a, base_abs[a], after_abs[a])  # type: ignore[index]
            for a in ABILITY_FIELDS
            if base_abs[a] != after_abs[a]  # type: ignore[index]
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
    args = parser.parse_args(sys.argv[1:])

    try:
        base_abs, base_pos = _load_base_abilities(args.player_id)
        calculate_ovr_tenths(base_abs, base_pos)
    except PlayerOvrError as exc:
        print(f"ERROR: {exc}")
        return
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return

    after_abs: dict[str, int] | None = None
    if args.spec:
        if not args.spec.exists():
            print(f"ERROR: spec file not found: {args.spec}")
            return
        try:
            after_abs = cast(
                dict[str, int], _apply_spec_patches(base_abs, args.spec)
            )
            calculate_ovr_tenths(after_abs, base_pos)
        except PlayerOvrError as exc:
            print(f"ERROR reading spec: {exc}")
            return
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
