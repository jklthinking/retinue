"""Concurrent task claims preserve one winner and one chain event."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from server.db import Actor, Task, TaskEvent, make_session_factory
from server.engine import Conflict, claim_task, create_task


def test_concurrent_claims_have_one_winner_and_one_conflict(tmp_path):
    factory = make_session_factory(tmp_path / "claim-race.db")
    with factory() as db:
        db.add_all(
            [
                Actor(id="publisher", kind="human"),
                Actor(id="agent-one", kind="agent"),
                Actor(id="agent-two", kind="agent"),
            ]
        )
        task = create_task(
            db,
            title="Concurrent claim",
            created_by="publisher",
            holder="publisher",
            open_dispatch=True,
        )
        db.commit()
        task_id = task.id

    ready = threading.Barrier(2)

    def attempt(claimant: str) -> tuple[str, str]:
        with factory() as db:
            task = db.get(Task, task_id)
            assert task is not None
            ready.wait(timeout=5)
            try:
                claimed = claim_task(db, task, claimant=claimant)
                assert claimed.holder == claimant
                assert claimed.open_dispatch is False
                db.commit()
            except Conflict:
                db.rollback()
                return "conflict", claimant
            return "winner", claimant

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(attempt, "agent-one"),
            pool.submit(attempt, "agent-two"),
        ]
        outcomes = [future.result(timeout=10) for future in futures]

    assert sorted(outcome for outcome, _claimant in outcomes) == [
        "conflict",
        "winner",
    ]
    winner = next(claimant for outcome, claimant in outcomes if outcome == "winner")

    with factory() as db:
        task = db.get(Task, task_id)
        assert task is not None
        claim_events = list(
            db.execute(
                select(TaskEvent)
                .where(TaskEvent.task_id == task_id, TaskEvent.seq > 1)
                .order_by(TaskEvent.seq)
            ).scalars()
        )

    assert task.holder == winner
    assert task.open_dispatch is False
    assert task.status == "queued"
    assert len(claim_events) == 1
    assert claim_events[0].who == winner
    assert claim_events[0].did == "接单"
    assert claim_events[0].from_status == "queued"
    assert claim_events[0].to_status == "queued"
    assert claim_events[0].from_holder == "publisher"
    assert claim_events[0].to_holder == winner
