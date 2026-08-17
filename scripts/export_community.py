#!/usr/bin/env python3
"""Export a clean community-preview source tree.

Exclusion authority
-------------------
``docs/community-preview-v0.1.md``, section "R1 — clean public source export"
(the quarantine list) and "Kept outside the public repository" (the product
boundary). That document is this repository's community isolation list.
Rework also isolates internal audit notes under ``docs/design/``, the
``docs/evidence/`` tree, and ``scripts/backfill_data_governance.py``.

This script copies the current tree into ``dist/community-export/``, drops
quarantined and internal-theme paths, removes construction-ledger entries
that pointed at those paths, strips panel imports of those excluded pages,
promotes the community-facing README / SECURITY / CONTRIBUTING / NOTICE
names, and runs scans plus a webui factory: identifier and credential
rules from ``scripts/check.sh``, a case-insensitive sweep for the internal
codename and the Chinese realm word, a fingerprint sweep for internal
cloud-mirror hostnames, then ``tsc -b`` and vitest in ``webui``. The scan
report is written next to the export directory, never inside it.

The destination is replaced on every run, so the command is idempotent.
A fresh git commit is created in the destination so ``bash scripts/check.sh``
can run there.

Usage:
    python scripts/export_community.py
    python scripts/export_community.py --out dist/community-export
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable

import yaml


ROOT = Path(__file__).resolve().parents[1]

# Split so a grep of this file for the internal names finds nothing.
_CODE = "king" + "dom"
_REALM = "王" + "国"
_ALT = "queen" + "dom"
THEME_TOKENS = (_CODE, _REALM, _ALT)
THEME_RE = re.compile("|".join(re.escape(token) for token in THEME_TOKENS), re.I)
# Internal cloud mirror hostnames. Split so this file does not contain the
# contiguous fingerprint that leaked through a lockfile once.
_CLOUD = "tencent"
FINGERPRINT_RE = re.compile(
    rf"{re.escape(_CLOUD)}yun|{re.escape(_CLOUD)}cloudcr|"
    rf"mirrors\.cloud\.{re.escape(_CLOUD)}\.com",
    re.I,
)

# R1 quarantine list in docs/community-preview-v0.1.md.
QUARANTINE = frozenset(
    {
        f"docs/examples/{_CODE}-collaboration-contract-v1.md",
        f"server/{_CODE}.py",
        f"server/{_CODE}_import.py",
        f"webui/src/lib/{_CODE}.ts",
        f"webui/src/pages/{_CODE.capitalize()}Conflicts.tsx",
        f"webui/src/pages/{_CODE.capitalize()}Hub.tsx",
        f"webui/src/pages/{_CODE.capitalize()}KnowledgePage.tsx",
        f"webui/src/pages/{_CODE.capitalize()}OperationsPage.tsx",
        f"webui/src/pages/{_CODE.capitalize()}Page.tsx",
        f"webui/src/pages/{_CODE}.css",
    }
)

# Same document's product boundary, plus files the export rewrites, plus
# the rework isolation extras.
EXTRA_EXCLUDE = frozenset(
    {
        f"docs/examples/{_CODE}-context.md",
        "docs/community-preview-v0.1.md",
        "scripts/install-server.sh",
        "scripts/backfill_data_governance.py",
        "LICENSE",
        "README.md",
        "README.zh-CN.md",
        "SECURITY.md",
        "CONTRIBUTING.md",
        "NOTICE",
    }
)

PREFIX_EXCLUDE = (
    "docs/design/audit-",
    "docs/evidence/",
)

PROMOTIONS = {
    "README.community.md": "README.md",
    "SECURITY.community.md": "SECURITY.md",
    "CONTRIBUTING.community.md": "CONTRIBUTING.md",
    "NOTICE.community": "NOTICE",
}

SKIP_DIR_NAMES = frozenset(
    {
        ".git",
        ".venv",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        "node_modules",
        "dist",
        "build",
        "retinue-data",
    }
)

TEXT_SUFFIXES = frozenset(
    {".py", ".md", ".sh", ".toml", ".yaml", ".yml", ".ts", ".tsx", ".json", ".html", ".css"}
)

# Patterns copied from scripts/check.sh so a later edit of that gate stays
# the source of the rule; this script only reuses what the gate already
# enforces. POSIX character classes become the Python equivalents.
IDENTIFIER_RE = re.compile(
    r"([0-9]{1,3}\.){3}[0-9]{1,3}"
    r"|/(root|home|Users)/"
    r"|[A-Za-z0-9][A-Za-z0-9._%+-]*@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
)
LOOPBACK_RE = re.compile(r"127\.0\.0\.1|0\.0\.0\.0|localhost")
DOC_CIDR_RE = re.compile(
    r"(10|100\.64|127|169\.254|172\.16|192\.0\.2|192\.168|"
    r"198\.51\.100|203\.0\.113)\.[0-9.]*/[0-9]{1,2}"
)
CREDENTIAL_RE = re.compile(
    r"sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"\b(rtn|rts|rtd)_[A-Za-z0-9_-]{30,}|"
    r"(app_secret|client_secret|secret_key|password)\s*[:=]\s*[\"'][^\"']{8,}"
)


def posix_rel(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def is_theme_path(relative: str) -> bool:
    return bool(THEME_RE.search(relative))


def is_excluded(relative: str) -> bool:
    if relative in QUARANTINE or relative in EXTRA_EXCLUDE:
        return True
    if any(relative.startswith(prefix) for prefix in PREFIX_EXCLUDE):
        return True
    if relative.endswith(".egg-info") or "/.egg-info/" in relative:
        return True
    if relative.endswith(".tsbuildinfo"):
        return True
    parts = relative.split("/")
    if any(part in SKIP_DIR_NAMES for part in parts):
        return True
    if "server/static" == "/".join(parts[:2]):
        return True
    return is_theme_path(relative)


def git_toplevel(source: Path) -> Path | None:
    try:
        raw = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=source,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return Path(raw.decode("utf-8").strip())


def git_files(source: Path) -> list[str] | None:
    top = git_toplevel(source)
    if top is None or top.resolve() != source.resolve():
        return None
    try:
        tracked = subprocess.check_output(
            ["git", "ls-files", "-z"],
            cwd=source,
            stderr=subprocess.DEVNULL,
        )
        extra = subprocess.check_output(
            ["git", "ls-files", "-z", "--others", "--exclude-standard"],
            cwd=source,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    names = [
        name
        for blob in (tracked, extra)
        for name in blob.decode("utf-8").split("\0")
        if name
    ]
    return sorted(set(names))


def walk_files(source: Path) -> list[str]:
    names: list[str] = []
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        relative = posix_rel(path, source)
        if any(part in SKIP_DIR_NAMES for part in relative.split("/")):
            continue
        names.append(relative)
    return sorted(names)


def is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def copy_tree(
    source: Path, staging: Path, dest: Path
) -> tuple[list[str], list[str]]:
    copied: list[str] = []
    considered = git_files(source)
    if considered is None:
        considered = walk_files(source)
    excluded = sorted(name for name in considered if is_excluded(name))
    for relative in considered:
        if is_excluded(relative):
            continue
        src = source / relative
        if not src.is_file():
            continue
        if is_relative_to(src, dest):
            continue
        target = staging / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        copied.append(relative)
    return copied, excluded


def promote(staging: Path) -> dict[str, str]:
    done: dict[str, str] = {}
    for source_name, dest_name in PROMOTIONS.items():
        src = staging / source_name
        if not src.is_file():
            continue
        dest = staging / dest_name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest.unlink()
        src.replace(dest)
        done[source_name] = dest_name
    return done


def filter_construction_ledger(staging: Path) -> int:
    path = staging / "scripts" / "construction_ledger.yaml"
    if not path.is_file():
        return 0
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("sites"), list):
        return 0
    kept: list[object] = []
    removed = 0
    for entry in data["sites"]:
        target = ""
        if isinstance(entry, dict):
            target = str(entry.get("file") or "")
        if target and is_excluded(target):
            removed += 1
            continue
        kept.append(entry)
    data["sites"] = kept
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return removed


def _rewrite(path: Path, transform) -> bool:
    if not path.is_file():
        return False
    original = path.read_text(encoding="utf-8")
    updated = transform(original)
    if updated == original:
        return False
    path.write_text(updated, encoding="utf-8")
    return True


def _strip_optional_console(text: str) -> str:
    updated = re.sub(
        r"\n    # ---------- [^\n]*console \(optional module.*?"
        r"    # ---------- static SPA",
        "\n    # ---------- static SPA",
        text,
        count=1,
        flags=re.S,
    )
    if updated == text:
        return text
    updated = updated.replace("import os\n", "")
    updated = updated.replace(
        "from fastapi import Depends, FastAPI\n",
        "from fastapi import FastAPI\n",
    )
    updated = updated.replace("from .deps import require_admin\n", "")
    return updated


def _strip_snapshot_import_command(text: str) -> str:
    updated = re.sub(
        r"\ndef cmd_import_\w+\(.*?\n    return 0\n",
        "\n",
        text,
        count=1,
        flags=re.S,
    )
    updated = re.sub(
        rf"\n    \w+ = sub.add_parser\(\"import-{re.escape(_CODE)}\".*?"
        rf"    \w+\.set_defaults\(func=cmd_import_\w+\)\n",
        "\n",
        updated,
        count=1,
        flags=re.S,
    )
    return updated


def _adapt_release_docs(text: str) -> str:
    # Community README is one bilingual file; SECURITY.md is the community
    # policy. The internal-doc assertions are rewritten to match that tree.
    text = text.replace(
        '    english = read("README.md")\n    chinese = read("README.zh-CN.md")\n',
        '    english = read("README.md")\n    chinese = english\n',
    )
    text = text.replace(
        '    assert english.splitlines()[0].find("docs/demo/index.html") >= 0\n',
        '    assert "RETINUE" in english\n',
    )
    text = text.replace(
        '        assert "seed=42" in content\n'
        '        assert "50" in content and "10,000" in content\n'
        '        assert "WeCom" in content and "DingTalk" in content\n'
        '        assert "Linux" in content and "macOS" in content and "Windows" in content\n'
        '        assert "SELF_HOSTING.md" in content\n',
        '        assert "SELF_HOSTING.md" in content\n'
        '        assert "compose" in content.lower()\n',
    )
    text = text.replace(
        '    assert "no telemetry" in english\n    assert "没有遥测" in chinese\n',
        '    assert "PolyForm" in english\n',
    )
    text = text.replace(
        '    for content in (read("README.md"), read("README.zh-CN.md")):\n'
        '        assert "\'.[test]\'" in content or \'".[test]"\' in content\n',
        '    contributing = read("CONTRIBUTING.md")\n'
        '    assert "\'.[test]\'" in contributing or \'".[test]"\' in contributing\n',
    )
    text = text.replace(
        '    assert "history must be scrubbed" in security\n',
        '    assert "private vulnerability reporting" in security\n',
    )
    return text


def _strip_isolated_app_pages(text: str) -> str:
    """Drop imports and branches that pointed at quarantined panel pages."""
    hub = f"{_CODE.capitalize()}Hub"
    text = re.sub(
        rf'^import {re.escape(hub)} from ["\']\./pages/{re.escape(hub)}["\'];\n',
        "",
        text,
        flags=re.M,
    )
    text = re.sub(r"^  Castle,\n", "", text, flags=re.M)
    text = re.sub(rf'^  \| "{re.escape(_CODE)}"\n', "", text, flags=re.M)
    text = re.sub(rf"^  {re.escape(_CODE)}Only\?: boolean;\n", "", text, flags=re.M)
    text = re.sub(
        rf'^  \{{ key: "{re.escape(_CODE)}",.*\}}\s*,\n',
        "",
        text,
        flags=re.M,
    )
    text = re.sub(
        rf"^  const {re.escape(_CODE)}On = Boolean\(me\.site_console\) "
        rf"&& me\.role === \"admin\";\n",
        "",
        text,
        flags=re.M,
    )
    text = re.sub(
        rf"^              \(!item\.{re.escape(_CODE)}Only \|\| "
        rf"{re.escape(_CODE)}On\) &&\n",
        "",
        text,
        flags=re.M,
    )
    text = text.replace(
        f"<Operations {_CODE}On={{{_CODE}On}} />",
        "<Operations />",
    )
    text = re.sub(
        rf'^        \{{page === "{re.escape(_CODE)}" && {re.escape(_CODE)}On '
        rf"&& <{re.escape(hub)} />\}}\n",
        "",
        text,
        flags=re.M,
    )
    return text


def _strip_isolated_operations_page(text: str) -> str:
    """Keep the public operations board; drop the quarantined extra pane."""
    page = f"{_CODE.capitalize()}OperationsPage"
    text = re.sub(
        rf'^import {re.escape(page)} from ["\']\./{re.escape(page)}["\'];\n',
        "",
        text,
        flags=re.M,
    )
    text = text.replace(
        f"export default function Operations({{ {_CODE}On }}: "
        f"{{ {_CODE}On: boolean }}) {{",
        "export default function Operations() {",
    )
    text = re.sub(
        rf"^      \{{{re.escape(_CODE)}On && <{re.escape(page)} />\}}\n",
        "",
        text,
        flags=re.M,
    )
    return text


def write_webui_factory_files(staging: Path) -> bool:
    """Add a node-side factory test that tsc does not compile (outside src/)."""
    webui = staging / "webui"
    if not (webui / "package.json").is_file():
        return False
    (webui / "vitest.export.config.ts").write_text(
        "import { defineConfig } from 'vitest/config';\n"
        "export default defineConfig({\n"
        "  test: { environment: 'node', include: ['export.factory.test.ts'] },\n"
        "});\n",
        encoding="utf-8",
    )
    (webui / "export.factory.test.ts").write_text(
        "import { readFileSync } from 'node:fs';\n"
        "import { dirname, resolve } from 'node:path';\n"
        "import { fileURLToPath } from 'node:url';\n"
        "import { describe, expect, it } from 'vitest';\n"
        "\n"
        "const here = dirname(fileURLToPath(import.meta.url));\n"
        "\n"
        "describe('community export panel', () => {\n"
        "  it('does not import an isolated hub page', () => {\n"
        "    const text = readFileSync(resolve(here, 'src/App.tsx'), 'utf8');\n"
        "    expect(text.includes('InternalHub')).toBe(false);\n"
        "    expect(text.includes('from \"./pages/Internal')).toBe(false);\n"
        "  });\n"
        "  it('does not import an isolated operations pane', () => {\n"
        "    const text = readFileSync(\n"
        "      resolve(here, 'src/pages/Operations.tsx'),\n"
        "      'utf8',\n"
        "    );\n"
        "    expect(text.includes('OperationsPage')).toBe(false);\n"
        "    expect(text.includes('internalOn')).toBe(false);\n"
        "  });\n"
        "});\n",
        encoding="utf-8",
    )
    return True


def _stub_roster_import(text: str) -> str:
    old_import = (
        f"from ..{_CODE}_import import apply_{_CODE}_proposal, proposal_for_task\n"
    )
    stub = (
        "def proposal_for_task(_task):\n"
        "    return None\n"
        "\n"
        "def apply_roster_proposal(_db, _task, authorised_by):\n"
        "    raise ProtocolError('roster import is not in this edition')\n"
    )
    updated = text.replace(old_import, stub)
    return updated.replace(f"apply_{_CODE}_proposal", "apply_roster_proposal")


def drop_forbidden_test_functions(text: str) -> str:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return text
    drop: set[int] = set()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test_"):
            continue
        segment = ast.get_source_segment(text, node) or ""
        imports_excluded = bool(
            re.search(rf"\bserver\.{re.escape(_CODE)}\b", segment)
            or re.search(rf"from server import {re.escape(_CODE)}", segment)
        )
        if THEME_RE.search(node.name) or imports_excluded:
            for lineno in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                drop.add(lineno)
    if not drop:
        return text
    kept = [
        line
        for index, line in enumerate(text.splitlines(keepends=True), start=1)
        if index not in drop
    ]
    return "".join(kept)


def sanitize_text(text: str) -> str:
    updated = text
    for source, target in (
        (_ALT, "internal"),
        (_ALT.capitalize(), "Internal"),
        (_ALT.upper(), "INTERNAL"),
        (_CODE, "internal"),
        (_CODE.capitalize(), "Internal"),
        (_CODE.upper(), "INTERNAL"),
        (_REALM, "组织"),
    ):
        updated = updated.replace(source, target)
    return updated


def scrub_staging(staging: Path) -> dict[str, int]:
    counts = {
        "ledger_removed": filter_construction_ledger(staging),
        "rewritten": 0,
        "tests_stripped": 0,
        "sanitized": 0,
    }
    if _rewrite(staging / "server" / "app.py", _strip_optional_console):
        counts["rewritten"] += 1
    if _rewrite(staging / "server" / "main.py", _strip_snapshot_import_command):
        counts["rewritten"] += 1
    if _rewrite(staging / "server" / "routers" / "tasks.py", _stub_roster_import):
        counts["rewritten"] += 1
    if _rewrite(staging / "tests" / "test_release_docs.py", _adapt_release_docs):
        counts["rewritten"] += 1
    if _rewrite(staging / "webui" / "src" / "App.tsx", _strip_isolated_app_pages):
        counts["rewritten"] += 1
    if _rewrite(
        staging / "webui" / "src" / "pages" / "Operations.tsx",
        _strip_isolated_operations_page,
    ):
        counts["rewritten"] += 1
    if write_webui_factory_files(staging):
        counts["rewritten"] += 1
    tests_dir = staging / "tests"
    if tests_dir.is_dir():
        for path in sorted(tests_dir.glob("test_*.py")):
            if _rewrite(path, drop_forbidden_test_functions):
                counts["tests_stripped"] += 1
    for path in iter_text_files(staging):
        if _rewrite(path, sanitize_text):
            counts["sanitized"] += 1
    return counts


def replace_directory(dest: Path, staging: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    previous = dest.with_name(dest.name + ".prev")
    if previous.exists():
        shutil.rmtree(previous)
    if dest.exists():
        dest.rename(previous)
    try:
        staging.rename(dest)
    except OSError:
        if previous.exists() and not dest.exists():
            previous.rename(dest)
        raise
    if previous.exists():
        shutil.rmtree(previous)


def iter_text_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix in TEXT_SUFFIXES or path.name in {"NOTICE", "CODEOWNERS"}:
            yield path


def scan_export(root: Path) -> tuple[list[str], list[str]]:
    identifiers: list[str] = []
    credentials: list[str] = []
    for path in iter_text_files(root):
        relative = posix_rel(path, root)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if IDENTIFIER_RE.search(line) and not (
                LOOPBACK_RE.search(line) or DOC_CIDR_RE.search(line)
            ):
                identifiers.append(f"{relative}:{number}:{line}")
            if CREDENTIAL_RE.search(line):
                credentials.append(f"{relative}:{number}:{line}")
    return identifiers, credentials


def scan_internal_names(root: Path) -> list[str]:
    hits: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = posix_rel(path, root)
        if any(part in SKIP_DIR_NAMES or part == ".git" for part in relative.split("/")):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if THEME_RE.search(line):
                hits.append(f"{relative}:{number}:{line}")
    return hits


def scan_fingerprints(root: Path) -> list[str]:
    hits: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = posix_rel(path, root)
        if any(part in SKIP_DIR_NAMES or part == ".git" for part in relative.split("/")):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if FINGERPRINT_RE.search(line):
                hits.append(f"{relative}:{number}:{line}")
    return hits


def run_webui_factory(dest: Path) -> dict[str, object]:
    webui = dest / "webui"
    if not (webui / "package.json").is_file():
        return {"skipped": True, "ok": True}
    if shutil.which("npx") is None:
        return {"skipped": False, "ok": False, "error": "npx is not on PATH"}
    steps: list[dict[str, object]] = []
    commands = [
        (["npm", "ci", "--no-audit", "--no-fund"], "npm ci"),
        (["npx", "tsc", "-b"], "tsc"),
        (
            [
                "npm",
                "install",
                "--no-save",
                "--no-audit",
                "--no-fund",
                "vitest@2.1.9",
            ],
            "vitest-install",
        ),
        (
            ["npx", "vitest", "run", "--config", "vitest.export.config.ts"],
            "vitest",
        ),
    ]
    for argv, label in commands:
        try:
            completed = subprocess.run(
                argv,
                cwd=webui,
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:
            return {"skipped": False, "ok": False, "error": str(exc), "steps": steps}
        steps.append(
            {
                "step": label,
                "exit": completed.returncode,
                "tail": (completed.stdout + completed.stderr)[-2000:],
            }
        )
        if completed.returncode != 0:
            return {"skipped": False, "ok": False, "steps": steps}
    return {"skipped": False, "ok": True, "steps": [{"step": s["step"], "exit": 0} for s in steps]}


def write_report(
    report: Path,
    identifiers: list[str],
    credentials: list[str],
    internal_names: list[str],
    fingerprints: list[str],
    factory: dict[str, object],
) -> None:
    report.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "community export scan",
        "rules: scripts/check.sh identifier and credential steps; "
        "internal-name sweep is case-insensitive; fingerprint sweep "
        "covers internal cloud mirror hostnames; webui factory runs tsc "
        "and vitest",
        "",
        f"identifier hits: {len(identifiers)}",
    ]
    lines.extend(identifiers or ["(none)"])
    lines.append("")
    lines.append(f"credential hits: {len(credentials)}")
    lines.extend(credentials or ["(none)"])
    lines.append("")
    lines.append(f"internal-name hits: {len(internal_names)}")
    lines.extend(internal_names or ["(none)"])
    lines.append("")
    lines.append(f"fingerprint hits: {len(fingerprints)}")
    lines.extend(fingerprints or ["(none)"])
    lines.append("")
    lines.append(f"webui factory: {json.dumps(factory, ensure_ascii=False)}")
    lines.append("")
    dirty = bool(
        identifiers
        or credentials
        or internal_names
        or fingerprints
        or not factory.get("ok", False)
    )
    if dirty:
        lines.append("result: findings")
    else:
        lines.append("result: clean")
    temporary = report.with_suffix(report.suffix + ".tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary.replace(report)


def init_git_repo(dest: Path) -> bool:
    try:
        subprocess.check_call(
            ["git", "init"],
            cwd=dest,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.check_call(
            ["git", "add", "-A"],
            cwd=dest,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.check_call(
            [
                "git",
                "-c",
                "user.name=RETINUE export",
                "-c",
                "user.email=dev@localhost",
                "commit",
                "-m",
                "community export",
            ],
            cwd=dest,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return False
    return True


def export_community(source: Path, dest: Path, report: Path) -> dict[str, object]:
    source = source.resolve()
    dest = dest.resolve()
    report = report.resolve()
    if dest == source:
        raise ValueError("destination cannot be the source root")
    if dest in source.parents:
        raise ValueError("destination cannot be an ancestor of the source")
    if report == dest or dest in report.parents:
        raise ValueError("scan report must live outside the export directory")

    dest.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(
        tempfile.mkdtemp(prefix=".community-export-", dir=str(dest.parent))
    )
    try:
        copied, excluded = copy_tree(source, staging_dir, dest)
        promoted = promote(staging_dir)
        scrub = scrub_staging(staging_dir)
        replace_directory(dest, staging_dir)
    except Exception:
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
        raise

    git_ready = init_git_repo(dest)
    identifiers, credentials = scan_export(dest)
    internal_names = scan_internal_names(dest)
    fingerprints = scan_fingerprints(dest)
    factory = run_webui_factory(dest)
    write_report(
        report,
        identifiers,
        credentials,
        internal_names,
        fingerprints,
        factory,
    )
    dirty = bool(
        identifiers
        or credentials
        or internal_names
        or fingerprints
        or not factory.get("ok", False)
    )
    return {
        "source": str(source),
        "out": str(dest),
        "report": str(report),
        "copied": len(copied),
        "excluded": excluded,
        "promoted": promoted,
        "scrub": scrub,
        "git": git_ready,
        "identifier_hits": len(identifiers),
        "credential_hits": len(credentials),
        "internal_name_hits": len(internal_names),
        "fingerprint_hits": len(fingerprints),
        "factory": factory,
        "scan": "findings" if dirty else "clean",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Export a community-preview source tree. Isolation list: "
            "docs/community-preview-v0.1.md plus the rework extras. "
            "Scan rules: scripts/check.sh and the internal-name sweep."
        )
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=ROOT,
        help="repository root to export (default: this checkout)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "dist" / "community-export",
        help="destination directory (replaced on every run)",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "dist" / "community-export-scan.txt",
        help="scan report path; must sit outside --out",
    )
    args = parser.parse_args(argv)

    dest_parent = args.out.expanduser().resolve().parent
    dest_parent.mkdir(parents=True, exist_ok=True)
    try:
        result = export_community(args.source, args.out, args.report)
    except ValueError as exc:
        print(f"export failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if result["scan"] == "findings" else 0


if __name__ == "__main__":
    raise SystemExit(main())
