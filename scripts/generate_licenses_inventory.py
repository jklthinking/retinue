#!/usr/bin/env python3
"""Write docs/licenses-inventory.md from the CycloneDX SBOMs in dist/.

Run ``scripts/generate_sbom.sh`` first so the JSON files exist. The inventory
is committed; the SBOMs stay in dist/.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON_SBOM = ROOT / "dist" / "sbom-python.cdx.json"
FRONTEND_SBOM = ROOT / "dist" / "sbom-frontend.cdx.json"
OUT = ROOT / "docs" / "licenses-inventory.md"

PERMISSIVE = {
    "0BSD",
    "AFL-2.1",
    "Apache-2.0",
    "BlueOak-1.0.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "BSD-3-Clause-Clear",
    "CC0-1.0",
    "ISC",
    "MIT",
    "MIT-0",
    "PSF-2.0",
    "Python-2.0",
    "Unlicense",
    "Zlib",
}
WEAK_COPYLEFT = {
    "CDDL-1.0",
    "EPL-1.0",
    "EPL-2.0",
    "LGPL-2.1",
    "LGPL-2.1-only",
    "LGPL-2.1-or-later",
    "LGPL-3.0",
    "LGPL-3.0-only",
    "LGPL-3.0-or-later",
    "MPL-2.0",
}
STRONG = {
    "AGPL-3.0",
    "AGPL-3.0-only",
    "AGPL-3.0-or-later",
    "BUSL-1.1",
    "GPL-2.0",
    "GPL-2.0-only",
    "GPL-2.0-or-later",
    "GPL-3.0",
    "GPL-3.0-only",
    "GPL-3.0-or-later",
    "SSPL-1.0",
}


def _license_ids(component: dict) -> list[str]:
    found: list[str] = []
    for entry in component.get("licenses") or []:
        if not isinstance(entry, dict):
            continue
        license_obj = entry.get("license") or {}
        if isinstance(license_obj, dict):
            if license_obj.get("id"):
                found.append(str(license_obj["id"]))
            elif license_obj.get("name"):
                name = str(license_obj["name"])
                if not name.startswith("License ::"):
                    found.append(name)
        expression = entry.get("expression")
        if not expression and isinstance(license_obj, dict):
            expression = license_obj.get("expression")
        if expression:
            found.append(str(expression))
    # Preserve order, drop duplicates.
    unique: list[str] = []
    for item in found:
        if item not in unique:
            unique.append(item)
    return unique or ["UNKNOWN"]


def _bucket(label: str) -> str:
    tokens = {
        part.strip()
        for part in label.replace("(", " ").replace(")", " ").replace(",", " ").split()
        if part.strip() and part.strip().upper() not in {"OR", "AND", "WITH"}
    }
    if any(token in STRONG for token in tokens):
        return "restricted"
    if any(token in WEAK_COPYLEFT for token in tokens):
        return "weak-copyleft"
    if any(token in PERMISSIVE for token in tokens) or "OR" in label.upper():
        if tokens and tokens.issubset(PERMISSIVE | {"OR", "AND"}):
            return "permissive"
        if any(token in PERMISSIVE for token in tokens) and not (
            tokens & (STRONG | WEAK_COPYLEFT)
        ):
            return "permissive"
    if label == "UNKNOWN":
        return "unknown"
    return "review"


def load_components(path: Path) -> list[dict[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows: list[dict[str, str]] = []
    for component in data.get("components") or []:
        if not isinstance(component, dict):
            continue
        name = str(component.get("name") or "")
        if not name:
            continue
        licenses = _license_ids(component)
        joined = " OR ".join(licenses)
        rows.append(
            {
                "name": name,
                "version": str(component.get("version") or ""),
                "licenses": joined,
                "bucket": _bucket(joined),
            }
        )
    rows.sort(key=lambda row: (row["bucket"], row["name"].lower(), row["version"]))
    return rows


def _table(rows: list[dict[str, str]]) -> str:
    lines = [
        "| Package | Version | License | Class |",
        "|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['name']} | {row['version']} | {row['licenses']} | {row['bucket']} |"
        )
    return "\n".join(lines)


def main() -> int:
    missing = [str(path) for path in (PYTHON_SBOM, FRONTEND_SBOM) if not path.is_file()]
    if missing:
        print(
            "generate_licenses_inventory: missing "
            + ", ".join(missing)
            + "; run scripts/generate_sbom.sh first",
            file=sys.stderr,
        )
        return 2
    python_rows = load_components(PYTHON_SBOM)
    frontend_rows = load_components(FRONTEND_SBOM)
    flagged = [
        row
        for row in python_rows + frontend_rows
        if row["bucket"] in {"restricted", "weak-copyleft", "review", "unknown"}
    ]
    restricted = [row for row in flagged if row["bucket"] == "restricted"]
    if restricted:
        conclusion = (
            "Do not ship this tree as-is: one or more production dependencies "
            "carry a restricted license. Review the flagged rows before a tag."
        )
    elif flagged:
        names = ", ".join(sorted({row["name"] for row in flagged}))
        conclusion = (
            "No GPL, AGPL, SSPL, or BUSL license appears in the resolved "
            f"production set. Flagged: {names}. certifi is MPL-2.0 (weak "
            "copyleft, file-level). That does not block a community-preview "
            "tag; re-check the flagged rows when versions move."
        )
    else:
        conclusion = (
            "Every listed production dependency is under a permissive SPDX "
            "license. That is compatible with self-hosting, forking, and the "
            "FSL-1.1 to Apache-2.0 conversion of RETINUE itself."
        )
    body = f"""# Dependency license inventory

Generated from the CycloneDX documents in `dist/` (`sbom-python.cdx.json`,
`sbom-frontend.cdx.json`). Re-run `bash scripts/generate_sbom.sh` then
`python scripts/generate_licenses_inventory.py` after a dependency change.
Those JSON files stay out of git. Generators: `cyclonedx-py` 7.3.1
(`cyclonedx-bom`) and `@cyclonedx/cyclonedx-npm` 4.0.0.

Python rows are the resolved install of `retinue[server]` in a throwaway
environment (the extra a hub needs). Frontend rows are the npm production
tree (`--omit dev`): `react`, `react-dom`, `lucide-react`, and whatever they
pull in. Test-only extras (`pytest`) and panel build tools (`vite`,
`typescript`) are not listed.

Classes: **permissive** (MIT, Apache-2.0, BSD, ISC, and similar),
**weak-copyleft** (MPL, LGPL, EPL), **restricted** (GPL, AGPL, SSPL, BUSL),
**unknown** / **review** (no SPDX id, or a name that is not on the lists
above). Dual licenses joined with OR are treated as permissive when at least
one option is permissive and none is restricted.

## Conclusion

{conclusion}

## Flagged rows

"""
    if flagged:
        body += _table(flagged) + "\n\n"
    else:
        body += "None.\n\n"
    body += "## Python (`retinue[server]`)\n\n"
    body += _table(python_rows) + "\n\n"
    body += "## Frontend (npm production)\n\n"
    body += _table(frontend_rows) + "\n"
    OUT.write_text(body, encoding="utf-8")
    print(f"wrote {OUT}")
    print(f"python components: {len(python_rows)}")
    print(f"frontend components: {len(frontend_rows)}")
    print(f"flagged: {len(flagged)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
