"""Sequence allocation stays duplicate- and gap-free across processes.

Each worker process opens its own engine and session factory against the same
database file, so these tests exercise the cross-process path that the old
in-process lock could never cover.
"""

from __future__ import annotations

import datetime as dt
import multiprocessing as mp

from sqlalchemy import select

from server.db import Actor, Task, TaskAttempt, TaskEvent, make_session_factory
from server.engine import (
    append_attempt,
    append_review_comment,
    create_task,
)

_WORKERS = 6
_PER_WORKER = 5


def _create_tasks(db_path, barrier, worker_index, results, task_id=None):
    factory = make_session_factory(db_path)
    barrier.wait(timeout=30)
    with factory() as db:
        for card in range(_PER_WORKER):
            task = create_task(
                db,
                title=f"Concurrent card {worker_index}-{card}",
                created_by="publisher",
                holder="publisher",
            )
            results.append(task.id)
        db.commit()


def _append_attempts(db_path, barrier, worker_index, results, task_id):
    factory = make_session_factory(db_path)
    barrier.wait(timeout=30)
    started = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    with factory() as db:
        task = db.get(Task, task_id)
        assert task is not None
        for run in range(_PER_WORKER):
            attempt, created = append_attempt(
                db,
                task,
                reporter_kind="operator",
                reporter_id="operator-console",
                duty=None,
                outcome="succeeded",
                started_at=started,
                ended_at=started,
                reason=None,
                exit_status=None,
                idempotency_key=f"worker-{worker_index}-run-{run}",
                is_privileged=True,
            )
            assert created
            results.append(attempt.seq)
        db.commit()


def _append_reviews(db_path, barrier, worker_index, results, task_id):
    factory = make_session_factory(db_path)
    barrier.wait(timeout=30)
    with factory() as db:
        task = db.get(Task, task_id)
        assert task is not None
        for note in range(_PER_WORKER):
            event, created = append_review_comment(
                db,
                task,
                who="reviewer",
                idempotency_key=f"worker-{worker_index}-note-{note}",
                body=f"note {note} from worker {worker_index}",
            )
            assert created
            results.append(event.seq)
        db.commit()


def _run_workers(target, db_path, task_id=None):
    context = mp.get_context("fork")
    with mp.Manager() as manager:
        results = manager.list()
        barrier = context.Barrier(_WORKERS)
        processes = [
            context.Process(
                target=target,
                args=(str(db_path), barrier, index, results, task_id),
            )
            for index in range(_WORKERS)
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=120)
        failures = [process.exitcode for process in processes if process.exitcode]
        assert not failures, f"worker processes failed: {failures}"
        return list(results)


def test_multiprocess_task_ids_have_no_duplicates_or_gaps(tmp_path):
    db_path = tmp_path / "ids.db"
    factory = make_session_factory(db_path)
    with factory() as db:
        db.add(Actor(id="publisher", kind="human"))
        db.commit()

    ids = _run_workers(_create_tasks, db_path)

    assert len(ids) == _WORKERS * _PER_WORKER
    assert len(set(ids)) == len(ids)
    prefix = ids[0].rsplit("-", 1)[0]
    serials = sorted(int(task_id.rsplit("-", 1)[1]) for task_id in ids)
    assert all(task_id.startswith(prefix + "-") for task_id in ids)
    assert serials == list(range(1, _WORKERS * _PER_WORKER + 1))


def test_multiprocess_attempt_seqs_have_no_duplicates_or_gaps(tmp_path):
    db_path = tmp_path / "attempts.db"
    factory = make_session_factory(db_path)
    with factory() as db:
        db.add(Actor(id="publisher", kind="human"))
        db.flush()
        task = create_task(
            db,
            title="Attempt race",
            created_by="publisher",
            holder="publisher",
        )
        db.commit()
        task_id = task.id

    seqs = _run_workers(_append_attempts, db_path, task_id)

    assert sorted(seqs) == list(range(1, _WORKERS * _PER_WORKER + 1))
    with factory() as db:
        stored = list(
            db.execute(
                select(TaskAttempt.seq)
                .where(TaskAttempt.task_id == task_id)
                .order_by(TaskAttempt.seq)
            ).scalars()
        )
    assert stored == list(range(1, _WORKERS * _PER_WORKER + 1))


def test_multiprocess_review_event_seqs_have_no_duplicates_or_gaps(tmp_path):
    db_path = tmp_path / "events.db"
    factory = make_session_factory(db_path)
    with factory() as db:
        db.add(Actor(id="publisher", kind="human"))
        db.add(Actor(id="reviewer", kind="human"))
        db.flush()
        task = create_task(
            db,
            title="Review race",
            created_by="publisher",
            holder="publisher",
        )
        db.commit()
        task_id = task.id

    seqs = _run_workers(_append_reviews, db_path, task_id)

    # The creation event holds seq 1; review events take the rest in order.
    expected = list(range(2, _WORKERS * _PER_WORKER + 2))
    assert sorted(seqs) == expected
    with factory() as db:
        stored = list(
            db.execute(
                select(TaskEvent.seq)
                .where(TaskEvent.task_id == task_id)
                .order_by(TaskEvent.seq)
            ).scalars()
        )
    assert stored == [1, *expected]
