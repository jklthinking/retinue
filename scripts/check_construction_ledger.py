#!/usr/bin/env python3
"""Construction-ledger gate: no permission-surface entity is constructed
without a written reason.

The permission surface is the set of durable, shared rows whose creation
decides who may do what: executors (``Actor``), nodes (``Node``), tasks
(``Task``), skills (``Skill``), knowledge sources (``KnowledgeSource``), and
the credential records (``User``, ``WebSession``, ``ApiToken``,
``NodeToken``). Every place in the tree that constructs one of these model
classes must be accounted for in ``scripts/construction_ledger.yaml``, and
accounting means a written reason naming who decides the change: an
administrator session, a credential scoped to the thing being changed, or an
explicit operator command. A site whose honest answer is "a scheduled job
read a file" is exactly what this gate exists to surface.

Matching is syntactic and by construction only: sources are parsed with
``ast`` and a site is a call expression that invokes one of the model class
names. Comments, docstrings, string literals, imports, and prose may mention
these names freely; none of them are call expressions. Test fixtures and the
demo seeder do construct entities, so they appear in the ledger like every
other site, with their own honest reasons -- never by a blanket exclusion.

Run ``--emit`` to print every discovered site as a ledger skeleton, which is
the starting point when this gate refuses a new site that is in fact a
decision.
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
LEDGER_PATH = Path(__file__).resolve().with_name("construction_ledger.yaml")
LEDGER_NAME = "scripts/construction_ledger.yaml"

# Model class name -> the permission-surface entity kind it constructs.
ENTITY_KINDS = {
    "Actor": "executor",
    "Node": "node",
    "Task": "task",
    "Skill": "skill",
    "SkillBinding": "skill binding",
    "KnowledgeSource": "knowledge source",
    "User": "credential record (user account)",
    "WebSession": "credential record (web session)",
    "ApiToken": "credential record (API token)",
    "NodeToken": "credential record (node token)",
}

# Who may decide a durable, shared change. "unresolved" is permitted only on
# a temporary entry with an expiry note, for overlap with work in flight.
DECIDERS = {
    "administrator-session": "an authenticated administrator session decides",
    "scoped-credential": "a credential scoped to the thing being changed decides",
    "operator-command": "an explicit operator command decides",
    "no-durable-change": "nothing shared or durable changes (test files only)",
    "unresolved": "nobody decides yet; temporary entry with an expiry note",
}

# A reason shorter than this is a name on a list, not an account of a decision.
MIN_REASON_CHARS = 32

EXCLUDED_DIR_NAMES = {
    ".git",
    ".venv",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    "build",
    "dist",
}


@dataclass(frozen=True)
class Site:
    """One syntactic construction of a permission-surface model class."""

    path: str  # repo-relative POSIX path
    entity: str  # model class name, e.g. "Actor"
    context: str  # enclosing function/class qualname, or "<module>"
    lineno: int


@dataclass(frozen=True)
class Entry:
    """One ledger entry: a written account of permitted construction sites."""

    file: str
    entity: str
    contexts: tuple[str, ...]  # empty means file+entity scope (tests only)
    decider: str
    reason: str
    temporary: bool
    expires: str

    @property
    def file_scoped(self) -> bool:
        return self.decider == "no-durable-change"


@dataclass
class Evaluation:
    violations: list[str] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    site_count: int = 0
    entry_count: int = 0


def discover_sites(source: str, relpath: str) -> list[Site]:
    """Return every construction of a tracked model class in one source file.

    Only ``ast.Call`` nodes qualify, which is what keeps this check from
    crying wolf: a comment, a string literal, or an import that mentions a
    class name is never a call.
    """
    tree = ast.parse(source, filename=relpath)
    sites: list[Site] = []

    def visit(node: ast.AST, context: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qualname = f"{context}.{child.name}" if context else child.name
                visit(child, qualname)
                continue
            if isinstance(child, ast.Call):
                called = child.func
                name = None
                if isinstance(called, ast.Name):
                    name = called.id
                elif isinstance(called, ast.Attribute):
                    name = called.attr
                if name in ENTITY_KINDS:
                    sites.append(Site(relpath, name, context or "<module>", child.lineno))
            visit(child, context)

    visit(tree, "")
    return sites


def iter_python_files(root: Path):
    """Repo-relative paths of every Python source, excluding volatile trees."""
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if any(
            part in EXCLUDED_DIR_NAMES or part.endswith(".egg-info")
            for part in rel.parts
        ):
            continue
        yield rel


def scan_tree(root: Path) -> list[Site]:
    sites: list[Site] = []
    for rel in iter_python_files(root):
        source = (root / rel).read_text(encoding="utf-8")
        sites.extend(discover_sites(source, rel.as_posix()))
    return sites


def _entry_problem(index: int, detail: str) -> str:
    return f"{LEDGER_NAME}: entry {index} is malformed: {detail}"


def load_ledger(path: Path) -> tuple[list[Entry], list[str]]:
    """Parse and validate the ledger; malformed entries are violations."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    problems: list[str] = []
    entries: list[Entry] = []
    if not isinstance(data, dict) or not isinstance(data.get("sites"), list):
        return [], [f"{LEDGER_NAME}: top level must be a mapping with a 'sites' list"]
    for index, raw in enumerate(data["sites"], start=1):
        if not isinstance(raw, dict):
            problems.append(_entry_problem(index, "entry must be a mapping"))
            continue
        file = raw.get("file")
        entity = raw.get("entity")
        decider = raw.get("decider")
        reason = raw.get("reason")
        contexts = raw.get("contexts")
        temporary = bool(raw.get("temporary", False))
        expires = str(raw.get("expires") or "").strip()
        label = f"entry {index} ({file}:{entity})"
        if not isinstance(file, str) or not file.endswith(".py") or ".." in file.split("/"):
            problems.append(_entry_problem(index, "'file' must be a repo-relative .py path"))
            continue
        if entity not in ENTITY_KINDS:
            problems.append(
                _entry_problem(index, f"unknown entity {entity!r}; known: {sorted(ENTITY_KINDS)}")
            )
            continue
        if decider not in DECIDERS:
            problems.append(
                _entry_problem(
                    index, f"{label}: decider must be one of {sorted(DECIDERS)}, got {decider!r}"
                )
            )
            continue
        if not isinstance(reason, str) or len(reason.strip()) < MIN_REASON_CHARS:
            problems.append(
                _entry_problem(
                    index,
                    f"{label}: 'reason' must be a written account of the decision "
                    f"(at least {MIN_REASON_CHARS} characters), not a name on a list",
                )
            )
            continue
        if decider == "no-durable-change":
            if not file.startswith("tests/"):
                problems.append(
                    _entry_problem(
                        index,
                        f"{label}: 'no-durable-change' is only accepted under tests/; "
                        "production code must name a real decider",
                    )
                )
                continue
            if contexts is not None:
                problems.append(
                    _entry_problem(
                        index,
                        f"{label}: test entries cover file+entity and must not list contexts",
                    )
                )
                continue
            contexts = []
        else:
            if (
                not isinstance(contexts, list)
                or not contexts
                or not all(isinstance(c, str) and c.strip() for c in contexts)
            ):
                problems.append(
                    _entry_problem(
                        index, f"{label}: 'contexts' must name the enclosing function(s)"
                    )
                )
                continue
        if decider == "unresolved" and not temporary:
            problems.append(
                _entry_problem(
                    index, f"{label}: 'unresolved' is only allowed on a temporary entry"
                )
            )
            continue
        if temporary and not expires:
            problems.append(
                _entry_problem(index, f"{label}: temporary entries need an 'expires' note")
            )
            continue
        entries.append(
            Entry(
                file=file,
                entity=entity,
                contexts=tuple(contexts),
                decider=decider,
                reason=reason.strip(),
                temporary=temporary,
                expires=expires,
            )
        )
    return entries, problems


def _unaccounted_message(site: Site) -> str:
    kind = ENTITY_KINDS[site.entity]
    return (
        f"{site.path}:{site.lineno}: constructs {site.entity} ({kind}) in "
        f"'{site.context}' — no ledger entry accounts for it.\n"
        f"  To permit it, add an entry to {LEDGER_NAME} with a written reason "
        "naming who decides this change: an administrator session, a credential "
        "scoped to the thing being changed, or an explicit operator command.\n"
        "  If the honest answer is \"a scheduled job read a file\" or \"nobody "
        "decides\", do not add an entry — route the change through a decision "
        "instead (an admin route, an operator command, or the protocol engine) "
        "so the row is created by a decider, not as a side effect."
    )


def _stale_message(entry: Entry, context: str | None) -> str:
    where = f"{entry.file} / {entry.entity}"
    if context is not None:
        where += f" / context '{context}'"
    return (
        f"{LEDGER_NAME}: stale entry — {where} no longer matches any "
        "construction site. Update or remove it: an entry that permits nothing "
        "will silently permit the next site written there."
    )


def evaluate(root: Path, ledger_path: Path) -> Evaluation:
    """Compare the tree against the ledger in both directions.

    Both directions fail: a construction site with no entry is an unaccounted
    side effect, and an entry matching no site is permission waiting for a
    site to arrive under it.
    """
    result = Evaluation()
    entries, problems = load_ledger(ledger_path)
    result.violations.extend(problems)
    result.entry_count = len(entries)
    sites = scan_tree(root)
    result.site_count = len(sites)

    exact: dict[tuple[str, str, str], Entry] = {}
    file_scope: dict[tuple[str, str], Entry] = {}
    for entry in entries:
        if entry.file_scoped:
            key = (entry.file, entry.entity)
            if key in file_scope:
                result.violations.append(
                    f"{LEDGER_NAME}: {entry.file} / {entry.entity} is accounted for "
                    "twice; keep exactly one entry per site."
                )
            file_scope[key] = entry
        else:
            for context in entry.contexts:
                key = (entry.file, entry.entity, context)
                if key in exact:
                    result.violations.append(
                        f"{LEDGER_NAME}: {entry.file} / {entry.entity} / '{context}' "
                        "is accounted for twice; keep exactly one entry per site."
                    )
                exact[key] = entry

    for entry in entries:
        if entry.file_scoped and (entry.file, entry.entity) in {
            (f, e) for (f, e, _c) in exact
        }:
            result.violations.append(
                f"{LEDGER_NAME}: {entry.file} / {entry.entity} is covered by both a "
                "test entry and a decider entry; keep exactly one."
            )

    matched_exact: set[tuple[str, str, str]] = set()
    matched_file_scope: set[tuple[str, str]] = set()
    for site in sites:
        key = (site.path, site.entity, site.context)
        file_key = (site.path, site.entity)
        if key in exact:
            matched_exact.add(key)
            continue
        if file_key in file_scope:
            matched_file_scope.add(file_key)
            continue
        result.violations.append(_unaccounted_message(site))

    for key in exact:
        if key not in matched_exact:
            result.violations.append(_stale_message(exact[key], key[2]))
    for key in file_scope:
        if key not in matched_file_scope:
            result.violations.append(_stale_message(file_scope[key], None))

    for entry in entries:
        if entry.temporary:
            contexts = ", ".join(entry.contexts) or "(file scope)"
            result.notices.append(
                f"TEMPORARY ledger entry: {entry.file} / {entry.entity} / {contexts} "
                f"— decider is 'unresolved'. Expires: {entry.expires}"
            )
    return result


def emit_skeleton(root: Path) -> str:
    """Print every discovered site as ledger YAML with TODO reasons."""
    grouped: dict[tuple[str, str], set[str]] = {}
    for site in scan_tree(root):
        grouped.setdefault((site.path, site.entity), set()).add(site.context)
    lines = ["sites:"]
    for (path, entity) in sorted(grouped):
        contexts = sorted(grouped[(path, entity)])
        lines.append(f"  - file: {path}")
        lines.append(f"    entity: {entity}")
        lines.append("    contexts:")
        lines.extend(f"      - {c}" for c in contexts)
        lines.append("    decider: TODO  # one of: " + ", ".join(sorted(DECIDERS)))
        lines.append("    reason: TODO  # who decides this change, in writing")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--emit",
        action="store_true",
        help="print discovered sites as a ledger skeleton instead of checking",
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--ledger", type=Path, default=LEDGER_PATH)
    args = parser.parse_args(argv)

    if args.emit:
        print(emit_skeleton(args.root))
        return 0

    result = evaluate(args.root, args.ledger)
    for notice in result.notices:
        print(notice)
    if result.violations:
        print()
        for violation in result.violations:
            print(violation)
            print()
        print(f"construction ledger: {len(result.violations)} problem(s); see above")
        return 1
    print(
        f"construction ledger: {result.site_count} construction site(s) accounted "
        f"for by {result.entry_count} entries "
        f"({len(result.notices)} temporary); OK"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
