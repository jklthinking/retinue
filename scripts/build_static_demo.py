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
for page in build_static_demo(args.output):
    print(page)
