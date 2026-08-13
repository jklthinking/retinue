#!/usr/bin/env python3
"""Generate the deterministic Retinue sample workspace."""

from __future__ import annotations

import argparse
from pathlib import Path

from core.demo import seed_sample


parser = argparse.ArgumentParser()
parser.add_argument("--seed", type=int, default=42)
parser.add_argument("--output", type=Path, default=Path("retinue-sample"))
args = parser.parse_args()
print(seed_sample(args.output, seed=args.seed))
