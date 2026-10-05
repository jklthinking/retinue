#!/usr/bin/env python3
"""Build docs/demo from the React panel plus frozen API snapshots."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


REPOSITORY = Path(__file__).resolve().parents[1]
if str(REPOSITORY) not in sys.path:
    sys.path.insert(0, str(REPOSITORY))

from core.panel_demo import build_panel_demo  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=REPOSITORY / "docs" / "demo")
    parser.add_argument("--language", choices=("zh-CN", "en"), default="zh-CN")
    parser.add_argument("--skip-install", action="store_true", help="Reuse dependencies already installed with npm ci; still build the UI.")
    parser.add_argument(
        "--skip-npm",
        action="store_true",
        help="Only refresh API JSON (for fast tests); skip the Vite production build.",
    )
    args = parser.parse_args()
    for path in build_panel_demo(args.output, skip_npm=args.skip_npm, language=args.language, install_dependencies=not args.skip_install):
        print(path.relative_to(args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
