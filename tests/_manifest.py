"""Read pyproject.toml on every interpreter the project claims to support.

Two tests reached for tomllib independently, and tomllib does not exist before
3.11 while pyproject declares support from 3.10. Both broke the declared minimum;
continuous integration caught each one separately. Reading the manifest belongs in
one place so a third test does not have to remember.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:  # 3.11 and newer
    import tomllib as _toml
except ModuleNotFoundError:  # 3.10, via the conditional test dependency
    import tomli as _toml  # type: ignore[no-redef]

ROOT = Path(__file__).resolve().parents[1]


def load_manifest() -> dict[str, Any]:
    """The parsed pyproject.toml."""
    return _toml.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def optional_dependencies() -> dict[str, list[str]]:
    return load_manifest()["project"]["optional-dependencies"]
