"""Behavioural tests for the construction-ledger gate.

The gate itself is a script step in ``scripts/check.sh`` because it reads as
policy; these tests exist so the policy cannot rot — they prove the check is
red when it should be, green on the tree as it stands, and silent on mere
mentions of an entity name.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
import re

from scripts import check_construction_ledger as ccl

REPO_ROOT = Path(__file__).resolve().parents[1]


def _write(tmp_path: Path, relpath: str, source: str) -> None:
    target = tmp_path / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(textwrap.dedent(source), encoding="utf-8")


def _write_ledger(tmp_path: Path, body: str) -> Path:
    ledger = tmp_path / "ledger.yaml"
    ledger.write_text(textwrap.dedent(body), encoding="utf-8")
    return ledger


def test_tree_as_it_stands_is_fully_accounted_for():
    """Not merely red: the real tree and the real ledger agree."""
    result = ccl.evaluate(REPO_ROOT, ccl.LEDGER_PATH)
    assert result.violations == []
    assert result.site_count > 0  # the scan found the sites it claims to cover
    # This assertion used to require the internal-import overlap to stay visible while
    # that redesign was in flight. It landed, those entries were replaced by real
    # deciders, and the assertion fired -- which is exactly what a tripwire is for.
    # It now guards the state we want to keep: nothing is permitted without a decider,
    # so a temporary entry cannot become permanent by habit.
    assert not any("TEMPORARY" in notice for notice in result.notices), (
        "a temporary ledger entry is outstanding; either give it a real decider or "
        "delete it and let the gate go red on the site"
    )


def test_unaccounted_construction_fails_and_teaches(tmp_path):
    _write(
        tmp_path,
        "server/nightly_sync.py",
        '''
        from server.db import Task

        def sync_from_disk(db, cards):
            for card in cards:
                db.add(Task(id=card["id"], title=card["title"],
                            created_by="import", holder="import"))
        ''',
    )
    ledger = _write_ledger(tmp_path, "sites: []\n")

    result = ccl.evaluate(tmp_path, ledger)

    assert len(result.violations) == 1
    message = result.violations[0]
    # Names the file, the line, the entity kind, and the remedy.
    assert "server/nightly_sync.py" in message
    assert "Task" in message and "(task)" in message
    assert "sync_from_disk" in message
    assert "construction_ledger.yaml" in message  # add a written reason, or...
    assert "route the change through a decision" in message  # ...decide instead
    assert re.search(r"server/nightly_sync\.py:\d+:", message)


def test_mentions_in_comments_strings_and_imports_never_fire(tmp_path):
    _write(
        tmp_path,
        "docs_example.py",
        '''
        """Explaining the schema: Actor(...) rows join Node(...) rows."""

        from server.db import Actor, Node, Skill  # an import constructs nothing

        # db.add(Task(title="x")) — a comment describing construction.
        PROSE = "KnowledgeSource(name='v') ApiToken( NodeToken( WebSession( User("

        def describe():
            text = "Task("  # a string literal, not a call
            return text
        ''',
    )
    assert ccl.discover_sites(
        (tmp_path / "docs_example.py").read_text(encoding="utf-8"), "docs_example.py"
    ) == []


def test_stale_entry_fails_instead_of_permitting_silently(tmp_path):
    _write(
        tmp_path,
        "server/thing.py",
        '''
        from server.db import Skill

        def register(db, name):
            db.add(Skill(name=name))
        ''',
    )
    ledger = _write_ledger(
        tmp_path,
        '''
        sites:
          - file: server/thing.py
            entity: Skill
            contexts:
              - register
              - removed_helper
            decider: administrator-session
            reason: >-
              An administrator session decides through the guarded route that
              calls this helper, and duplicates are refused.
        ''',
    )

    result = ccl.evaluate(tmp_path, ledger)

    assert len(result.violations) == 1
    assert "stale" in result.violations[0]
    assert "removed_helper" in result.violations[0]


def test_accounted_construction_passes(tmp_path):
    _write(
        tmp_path,
        "server/thing.py",
        '''
        from server.db import Skill

        def register(db, name):
            db.add(Skill(name=name))
        ''',
    )
    ledger = _write_ledger(
        tmp_path,
        '''
        sites:
          - file: server/thing.py
            entity: Skill
            contexts:
              - register
            decider: administrator-session
            reason: >-
              An administrator session decides through the guarded route that
              calls this helper, and duplicates are refused.
        ''',
    )

    result = ccl.evaluate(tmp_path, ledger)

    assert result.violations == []
    assert result.site_count == 1


def test_unresolved_decider_needs_temporary_and_expiry(tmp_path):
    _write(
        tmp_path,
        "server/import_job.py",
        '''
        from server.db import Node

        def nightly(db, node_id):
            db.add(Node(id=node_id))
        ''',
    )
    ledger = _write_ledger(
        tmp_path,
        '''
        sites:
          - file: server/import_job.py
            entity: Node
            contexts:
              - nightly
            decider: unresolved
            reason: >-
              Nobody decides: a scheduled job creates the row as a side effect
              of reading a file.
        ''',
    )

    result = ccl.evaluate(tmp_path, ledger)

    assert any("unresolved" in v and "temporary" in v for v in result.violations)


def test_short_reason_is_a_name_on_a_list(tmp_path):
    _write(
        tmp_path,
        "server/thing.py",
        '''
        from server.db import Skill

        def register(db, name):
            db.add(Skill(name=name))
        ''',
    )
    ledger = _write_ledger(
        tmp_path,
        '''
        sites:
          - file: server/thing.py
            entity: Skill
            contexts:
              - register
            decider: operator-command
            reason: needed for the demo
        ''',
    )

    result = ccl.evaluate(tmp_path, ledger)

    assert any("reason" in v for v in result.violations)
