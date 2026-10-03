#!/usr/bin/env python3
"""Build the committed, offline seed-42 demo site."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


REPOSITORY = Path(__file__).resolve().parents[1]
if str(REPOSITORY) not in sys.path:
    sys.path.insert(0, str(REPOSITORY))

from core.static_demo import build_static_demo  # noqa: E402


parser = argparse.ArgumentParser()
parser.add_argument("--output", type=Path, default=REPOSITORY / "docs" / "demo")
args = parser.parse_args()
if (args.output / ".retinue-panel-demo").is_file():
    from core.panel_demo import build_panel_demo
    pages = build_panel_demo(args.output)
else:
    pages = build_static_demo(args.output)
for page in pages:
    print(page)
