"""The server-to-file export is deterministic, one-way, and lint-clean."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import yaml
from sqlalchemy import select

from core.protocol.task import lint_path
from scripts.export_cards import CARD_FIELD_ORDER, EVENT_FIELD_ORDER, export_cards
from server.db import (
    Actor,
    ApiToken,
    Node,
    NodeToken,
    RuntimeSession,
    SessionCapture,
    Task,
    TaskDependency,
    TaskEvent,
    User,
    WebSession,
    make_session_factory,
)
from server.engine import create_task, update_task

# Neutral synthetic sentinels: if any of these reaches the export, private
# data leaked into the file tree.
SESSION_BODY_SENTINEL = "SESSION-BODY-SENTINEL"
CAPTURE_BODY_SENTINEL = "CAPTURE-BODY-SENTINEL"
API_TOKEN_SENTINEL = "api-token-hash-sentinel-value"
NODE_TOKEN_SENTINEL = "node-token-hash-sentinel-value"
WEB_SESSION_SENTINEL = "web-session-hash-sentinel-value"
PASSWORD_HASH_SENTINEL = "password-hash-sentinel-value"
NODE_HOSTNAME_SENTINEL = "node-hostname-sentinel"
REVIEW_NOTE_SENTINEL = "review-note-sentinel"
REVIEW_PAYLOAD_SENTINEL = "review-payload-sentinel"

PRIVATE_SENTINELS = (
    SESSION_BODY_SENTINEL,
    CAPTURE_BODY_SENTINEL,
    API_TOKEN_SENTINEL,
    NODE_TOKEN_SENTINEL,
    WEB_SESSION_SENTINEL,
    PASSWORD_HASH_SENTINEL,
    NODE_HOSTNAME_SENTINEL,
    REVIEW_NOTE_SENTINEL,
    REVIEW_PAYLOAD_SENTINEL,
)

SNAPSHOT_MODELS = (
    Actor,
    User,
    WebSession,
    ApiToken,
    Node,
    NodeToken,
    Task,
    TaskDependency,
    TaskEvent,
    RuntimeSession,
    SessionCapture,
)


def _seed(db_path: Path) -> dict[str, str]:
    """Build a small but realistic board plus private bystander data."""
    factory = make_session_factory(db_path)
    with factory() as db:
        db.add_all(
            [
                Actor(id="queen", kind="human"),
                Actor(id="agent-one", kind="agent"),
            ]
        )
        alpha = create_task(
            db,
            title="Alpha card",
            created_by="queen",
            holder="agent-one",
            dept="throne",
            priority="high",
            acceptance=["导出结果可通过既有 lint"],
        )
        beta = create_task(
            db,
            title="Beta card",
            created_by="queen",
            holder="queen",
            depends_on=[alpha.id],
        )
        gamma = create_task(
            db,
            title="Gamma card",
            created_by="queen",
            holder="agent-one",
        )
        update_task(
            db, alpha, who="queen", is_privileged=True, status="doing", note="开始处理"
        )
        update_task(
            db,
            gamma,
            who="agent-one",
            is_privileged=True,
            status="doing",
            note="认领",
        )
        update_task(
            db,
            gamma,
            who="agent-one",
            is_privileged=True,
            status="done",
            note="完成",
            refs=["docs/example.md"],
        )
        gamma.archived = True
        # A review event carries server-only payload text; it must not leak
        # into the protocol chain of the exported card.
        db.add(
            TaskEvent(
                task_id=alpha.id,
                seq=90,
                who="queen",
                did=f"审阅意见 {REVIEW_NOTE_SENTINEL}",
                at="2026-08-01T00:00:00.000000Z",
                from_status="doing",
                to_status="doing",
                from_holder="agent-one",
                to_holder="agent-one",
                event_type="review_comment",
                event_key="review-sentinel",
                payload_json=json.dumps({"body": REVIEW_PAYLOAD_SENTINEL}),
            )
        )
        # Private bystander data: session bodies, token hashes, topology.
        session = RuntimeSession(
            actor_id="agent-one",
            runtime="claude-code",
            external_id="ext-1",
            content_hash="content-hash-1",
            title="runtime session",
            messages_json=json.dumps(
                [{"role": "user", "content": SESSION_BODY_SENTINEL}]
            ),
        )
        db.add(session)
        db.flush()
        db.add(
            SessionCapture(
                session_id=session.id,
                actor_id="agent-one",
                markdown=CAPTURE_BODY_SENTINEL,
                target_path="vault/capture.md",
            )
        )
        db.add(ApiToken(token_hash=API_TOKEN_SENTINEL, actor_id="agent-one"))
        db.add(Node(id="node-a", hostname=NODE_HOSTNAME_SENTINEL, platform="test-os"))
        db.add(NodeToken(token_hash=NODE_TOKEN_SENTINEL, node_id="node-a"))
        user = User(username="operator-a", password_hash=PASSWORD_HASH_SENTINEL)
        db.add(user)
        db.flush()
        db.add(
            WebSession(
                token_hash=WEB_SESSION_SENTINEL,
                user_id=user.id,
                expires_at=dt.datetime(2027, 1, 1, tzinfo=dt.timezone.utc),
            )
        )
        db.commit()
        return {"alpha": alpha.id, "beta": beta.id, "gamma": gamma.id}


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        candidate.name: candidate.read_bytes()
        for candidate in sorted(root.iterdir())
        if candidate.is_file()
    }


def _db_snapshot(factory) -> str:
    """A logical dump of every table the export could conceivably touch."""
    tables: dict[str, list[dict[str, object]]] = {}
    with factory() as db:
        for model in SNAPSHOT_MODELS:
            columns = [column.name for column in model.__table__.columns]
            rows = []
            for row in db.execute(select(model)).scalars():
                rows.append({key: getattr(row, key) for key in columns})
            tables[model.__tablename__] = rows
    return json.dumps(tables, sort_keys=True, default=str)


def _export(factory, out: Path) -> dict:
    with factory() as db:
        return export_cards(db, out)


def test_export_twice_is_byte_identical(tmp_path):
    db_path = tmp_path / "retinue.db"
    _seed(db_path)
    factory = make_session_factory(db_path)
    out = tmp_path / "repo" / "tasks"

    _export(factory, out)
    first = _tree_bytes(out)
    assert first, "expected at least one exported card"

    result = _export(factory, out)
    assert _tree_bytes(out) == first
    assert result["stale"] == []


def test_exported_cards_pass_existing_lint_unmodified(tmp_path):
    db_path = tmp_path / "retinue.db"
    ids = _seed(db_path)
    factory = make_session_factory(db_path)
    out = tmp_path / "repo" / "tasks"
    _export(factory, out)

    results = lint_path(out)
    assert results, "lint found no cards to check"
    assert all(error is None for _path, error in results)

    card = yaml.safe_load((out / f"{ids['alpha']}.yaml").read_text(encoding="utf-8"))
    assert list(card) == list(CARD_FIELD_ORDER)
    assert card["depends_on"] == []
    assert all(
        list(event) == list(EVENT_FIELD_ORDER) for event in card["chain"]
    )
    beta = yaml.safe_load((out / f"{ids['beta']}.yaml").read_text(encoding="utf-8"))
    assert beta["depends_on"] == [ids["alpha"]]
    # The archived flag is server bookkeeping; the card itself still exports.
    assert (out / f"{ids['gamma']}.yaml").is_file()


def test_export_writes_nothing_and_reads_nothing_back(tmp_path):
    db_path = tmp_path / "retinue.db"
    ids = _seed(db_path)
    factory = make_session_factory(db_path)
    out = tmp_path / "repo" / "tasks"

    db_bytes_before = db_path.read_bytes()
    snapshot_before = _db_snapshot(factory)
    _export(factory, out)
    assert db_path.read_bytes() == db_bytes_before
    assert _db_snapshot(factory) == snapshot_before

    # Sabotage the exported files. If the export read them back, the corrupt
    # card would break it or the missing one would stay missing; instead both
    # are deterministically rewritten from the database alone.
    first = _tree_bytes(out)
    corrupt = out / f"{ids['alpha']}.yaml"
    corrupt.write_text("garbage: [unclosed", encoding="utf-8")
    (out / f"{ids['beta']}.yaml").unlink()

    _export(factory, out)
    assert _tree_bytes(out) == first
    assert db_path.read_bytes() == db_bytes_before
    assert _db_snapshot(factory) == snapshot_before


def test_export_omits_sessions_tokens_and_topology(tmp_path):
    db_path = tmp_path / "retinue.db"
    ids = _seed(db_path)
    factory = make_session_factory(db_path)
    out = tmp_path / "repo" / "tasks"
    _export(factory, out)

    exported = {
        candidate.name: candidate.read_text(encoding="utf-8")
        for candidate in out.iterdir()
        if candidate.is_file()
    }
    assert set(exported) == {f"{task_id}.yaml" for task_id in ids.values()}
    blob = "\n".join(exported.values())
    for sentinel in PRIVATE_SENTINELS:
        assert sentinel not in blob

    alpha = yaml.safe_load(exported[f"{ids['alpha']}.yaml"])
    # Only protocol task events survive: the create and doing events, never
    # the review comment with its payload.
    assert [event["to_status"] for event in alpha["chain"]] == ["queued", "doing"]
