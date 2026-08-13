"""Canonical task-event timestamps and sequence ordering."""

from __future__ import annotations

from contextlib import contextmanager
import datetime as dt
import os
import time

import pytest

import core.protocol.task as task_protocol
from server.db import Actor, Task, make_session_factory
from server.engine import create_task, task_to_dict, update_task


@contextmanager
def _local_timezone(zone: str):
    previous = os.environ.get("TZ")
    os.environ["TZ"] = zone
    time.tzset()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time.tzset()


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="requires TZ environment support")
def test_same_second_events_are_distinct_sortable_utc_across_local_zones(
    tmp_path, monkeypatch
):
    moments = iter(
        (
            dt.datetime(2026, 8, 7, 1, 2, 3, 1, tzinfo=dt.timezone.utc),
            dt.datetime(2026, 8, 7, 1, 2, 3, 2, tzinfo=dt.timezone.utc),
        )
    )

    class SequenceDateTime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            value = next(moments)
            return value if tz is None else value.astimezone(tz)

    monkeypatch.setattr(task_protocol, "datetime", SequenceDateTime)
    factory = make_session_factory(tmp_path / "events.db")
    with factory() as db:
        db.add_all(
            [
                Actor(id="owner", kind="human"),
                Actor(id="worker", kind="agent"),
            ]
        )
        with _local_timezone("Etc/GMT-8"):
            task = create_task(
                db,
                title="Timestamp ordering",
                created_by="owner",
                holder="worker",
            )
        with _local_timezone("Etc/GMT+5"):
            update_task(
                db,
                task,
                who="worker",
                is_privileged=False,
                status="doing",
                note="started",
            )
        db.commit()
        task_id = task.id

    with factory() as db:
        stored = db.get(Task, task_id)
        assert stored is not None
        events = task_to_dict(stored)["chain"]

    timestamps = [event["at"] for event in events]
    assert timestamps == [
        "2026-08-07T01:02:03.000001Z",
        "2026-08-07T01:02:03.000002Z",
    ]
    assert timestamps[0][:19] == timestamps[1][:19]
    assert timestamps == sorted(timestamps)


def test_server_chain_order_is_seq_even_when_legacy_at_strings_sort_differently(
    tmp_path,
):
    factory = make_session_factory(tmp_path / "sequence.db")
    with factory() as db:
        db.add_all(
            [
                Actor(id="owner", kind="human"),
                Actor(id="worker", kind="agent"),
            ]
        )
        task = create_task(
            db,
            title="Sequence authority",
            created_by="owner",
            holder="worker",
        )
        update_task(
            db,
            task,
            who="worker",
            is_privileged=False,
            status="doing",
            note="second event",
        )
        task.events[0].at = "2026-08-07T09:00+08:00"
        task.events[1].at = "2026-08-06T21:00-05:00"
        db.commit()
        task_id = task.id

    with factory() as db:
        stored = db.get(Task, task_id)
        assert stored is not None
        events = task_to_dict(stored)["chain"]

    assert [event["did"] for event in events] == ["task created", "second event"]
    assert events[0]["at"] > events[1]["at"]
