"""Retinue Server — database-backed product core for the Retinue protocol."""

from __future__ import annotations

import re
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

# The published version lives in pyproject.toml (PEP 440, e.g. 0.2.0a3).
# Wheel names, GET /api/health, and the next git tag (v<that string>) must
# all use this same spelling. The checkout is preferred over install
# metadata so an editable tree does not keep serving a stale install.


def _package_version() -> str:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    if pyproject.is_file():
        match = re.search(
            r'(?m)^version\s*=\s*"([^"]+)"',
            pyproject.read_text(encoding="utf-8"),
        )
        if match:
            return match.group(1)
    try:
        return version("retinue")
    except PackageNotFoundError as exc:
        raise RuntimeError("cannot determine the RETINUE version") from exc


__version__ = _package_version()
