from __future__ import annotations

import argparse
from collections.abc import Sequence

from installer import __version__
from installer.app import run_gui, self_test


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="FLDailyEditInstaller")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--version", action="store_true")
    group.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.version:
        print(__version__)
        return 0
    if args.self_test:
        return self_test()
    return run_gui()


if __name__ == "__main__":
    raise SystemExit(main())
