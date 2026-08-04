"""CLI integrity checker for encrypted Football Life 2026 EDIT files."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from editor.editfile import EditFile
import editor.crypto as crypto


def check_corruption(edit_path: Path) -> int:
    """Decrypt and validate an EDIT file; return the number of errors."""
    print(f"=== FL26 integrity check: {edit_path} ===")
    if not edit_path.exists():
        print(f"File does not exist: {edit_path}")
        return 1

    temp_dir = crypto.decrypt(edit_path)
    try:
        data_dat = temp_dir / "data.dat"
        ef = EditFile(data_dat)
        ef.load()
        report = ef.validate_integrity()

        print(f"Metrics: {report['metrics']}")
        for warning in report["warnings"]:
            print(f"WARNING: {warning}")
        for error in report["errors"]:
            print(f"ERROR: {error}")

        if report["valid"]:
            print("PASS: save structure matches known-good FL26 invariants")
        else:
            print(f"FAIL: {len(report['errors'])} integrity error(s)")
        return len(report["errors"])
    finally:
        crypto.cleanup_temp(temp_dir)


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("output/EDIT00000000")
    raise SystemExit(1 if check_corruption(path) else 0)
