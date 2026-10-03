"""Freshness is about collection, not the age or acceptance of historical work."""
import datetime as dt
import json

import pytest

from server.data_health import HISTORY_SOURCES_ENV, build_data_health
from server.db import Actor, Node, RuntimeSession, Task, TaskEvent, TokenUsage, make_session_factory

NOW = dt.datetime(2026, 8, 12, 12, tzinfo=dt.timezone.utc)


def checks(body):
    return {check["key"]: check for check in body["checks"]}


def test_old_history_recently_synced_is_fresh_and_check_never_mutates(tmp_path):
    factory = make_session_factory(tmp_path / "health.db")
    with factory() as db:
        db.add(Actor(id="worker-a", node="device-a", runtime="codex", model="model-a"))
        db.add(Node(id="device-a", updated_at=NOW, runtimes_probed_at=NOW))
        db.add(RuntimeSession(actor_id="worker-a", runtime="codex", external_id="history-a", content_hash="hash-a", updated_at=NOW-dt.timedelta(days=40), synced_at=NOW, summary="private transcript should never escape"))
        db.add(TokenUsage(actor_id="worker-a", runtime="codex", date="2026-07-01", input_tokens=9, updated_at=NOW))
        db.add(Task(id="task-20260701-001", title="private task title", created_by="worker-a", holder="worker-a", status="done", lease_expires_at=NOW-dt.timedelta(days=40)))
        db.commit()
        result = build_data_health(db, now=NOW)
        assert checks(result)["session_sync"]["status"] == "good"
        assert checks(result)["usage_sync"]["status"] == "good"
        assert checks(result)["task_leases"]["total"] == 0
        assert not db.dirty and not db.new and not db.deleted
        assert "private" not in json.dumps(result)
        assert db.get(Task, "task-20260701-001").status == "done"


def test_inactive_sources_expired_open_lease_and_future_clock_are_visible(tmp_path):
    factory = make_session_factory(tmp_path / "health.db")
    with factory() as db:
        db.add(Actor(id="worker-a", node="device-a", runtime="codex", model="model-a"))
        db.add(Node(id="device-a", updated_at=NOW+dt.timedelta(hours=1), runtimes_probed_at=None))
        db.add(RuntimeSession(actor_id="worker-a", runtime="codex", external_id="session-a", content_hash="hash-a", synced_at=NOW-dt.timedelta(hours=1)))
        db.add(Task(id="task-20260812-001", title="task", created_by="worker-a", holder="worker-a", status="handoff", lease_expires_at=NOW-dt.timedelta(seconds=1)))
        db.commit()
        result = checks(build_data_health(db, now=NOW))
        assert result["node_telemetry"]["items"][0]["state"] == "clock_skew"
        assert result["runtime_inventory"]["items"][0]["state"] == "unknown"
        assert result["session_sync"]["items"][0]["state"] == "stale"
        assert result["task_leases"]["items"][0]["state"] == "expired"
        assert db.get(Task, "task-20260812-001").status == "handoff"


def test_transport_services_are_not_workers_and_duplicate_identity_is_only_candidate(tmp_path):
    factory = make_session_factory(tmp_path / "health.db")
    with factory() as db:
        db.add_all([
            Actor(id="worker-a", node="device-a", runtime="codex", model="model-a"),
            Actor(id="worker-b", node="DEVICE-A", runtime="openai-codex", model="MODEL-A"),
            Actor(id="device-a-session-sync", node="device-a", runtime="multi", model="session-index-v2"),
            Actor(id="old-worker", node="device-a", runtime="codex", model="model-a", disabled=True),
            Actor(id="worker-c", runtime="codex", model="auto"),
        ])
        db.commit()
        body = checks(build_data_health(db, now=NOW))
        assert body["worker_identity"]["total"] == 3
        assert body["worker_identity"]["observed"] == 2
        assert {item["id"] for item in body["worker_duplicates"]["items"]} == {"worker-a", "worker-b"}
        assert not db.get(Actor, "worker-a").disabled
        assert not db.get(Actor, "worker-b").disabled


def test_event_chain_coverage_cannot_be_satisfied_by_other_tasks_events(tmp_path):
    from server.helpers import build_data_catalog

    factory = make_session_factory(tmp_path / "health.db")
    with factory() as db:
        db.add(Actor(id="worker-a", node="device-a", runtime="codex", model="model-a"))
        for number in [1, 2]:
            db.add(Task(id=f"task-20260812-{number:03d}", title="task", created_by="worker-a", holder="worker-a"))
        for sequence in [1, 2, 3]:
            db.add(TaskEvent(task_id="task-20260812-001", seq=sequence, who="worker-a", did="record", at=NOW.isoformat()))
        db.commit()
        result = build_data_catalog(db)
        chain = next(item for item in result["quality"]["checks"] if item["key"] == "event_chain")
        assert chain["observed"] == 1
        assert chain["total"] == 2
        assert chain["status"] == "attention"
        assert result["summary"]["events"] == 3


def test_registry_observation_timestamps_are_utc_and_never_imply_execution():
    from server.deps import ONLINE_WINDOW
    from server.helpers import actor_to_dict
    from server.routers.nodes import observation_time

    naive = NOW.replace(tzinfo=None)
    actor = Actor(id="worker-a", kind="agent", disabled=False, last_seen_at=naive)
    cutoff = NOW - ONLINE_WINDOW
    result = actor_to_dict(actor, cutoff)
    assert result["last_seen_at"].endswith("+00:00")
    assert result["activity_basis"] == "authenticated_api_activity"
    assert result["online"]
    actor.disabled = True
    assert not actor_to_dict(actor, cutoff)["online"]
    actor.disabled = False
    actor.last_seen_at = naive + dt.timedelta(hours=8)
    assert not actor_to_dict(actor, cutoff)["online"]
    assert observation_time(naive).endswith("+00:00")
    assert observation_time(None) is None


def _history_rule(**overrides):
    return {"actor_id": "legacy-session-sync", "runtime": "codex",
            "before": (NOW - dt.timedelta(days=1)).isoformat(), **overrides}


def _legacy_source(db):
    db.add(Actor(id="legacy-session-sync", node="device-a", runtime="codex", model="session-index-v2"))
    db.add(RuntimeSession(actor_id="legacy-session-sync", runtime="codex", external_id="old-source",
                          content_hash="old-hash", privacy="summary", summary="retained private history",
                          updated_at=NOW-dt.timedelta(days=90), synced_at=NOW-dt.timedelta(days=2)))
    db.commit()


def test_operator_history_cutoff_reclassifies_source_without_health_credit_or_mutation(tmp_path, monkeypatch):
    monkeypatch.setenv(HISTORY_SOURCES_ENV, json.dumps([_history_rule()]))
    factory = make_session_factory(tmp_path / "history-health.db")
    with factory() as db:
        _legacy_source(db)
        result = checks(build_data_health(db, now=NOW))
        assert result["session_sync"]["total"] == 0
        history = result["session_history"]
        assert (history["status"], history["observed"], history["total"], history["retained"], history["issue_count"]) == ("info", 0, 1, 1, 0)
        item = history["items"][0]
        assert item["state"] == "retained_history" and item["runtime"] == "codex"
        assert item["observed_at"] == (NOW-dt.timedelta(days=2)).isoformat()
        assert item["history_before"] == _history_rule()["before"]
        assert "session_history_config" not in result
        assert not db.dirty and not db.new and not db.deleted
        assert not db.get(Actor, "legacy-session-sync").disabled
        assert db.query(RuntimeSession).count() == 1
        assert db.query(RuntimeSession).one().summary == "retained private history"
        assert "retained private history" not in json.dumps(result)


@pytest.mark.parametrize("new_at", [NOW-dt.timedelta(days=1), NOW])
def test_cutoff_or_newer_report_reactivates_exact_source_and_keeps_old_rows(tmp_path, monkeypatch, new_at):
    monkeypatch.setenv(HISTORY_SOURCES_ENV, json.dumps([_history_rule()]))
    factory = make_session_factory(tmp_path / "history-health.db")
    with factory() as db:
        _legacy_source(db)
        db.add(RuntimeSession(actor_id="legacy-session-sync", runtime="codex", external_id="new-source",
                              content_hash="new-hash", updated_at=NOW-dt.timedelta(days=100), synced_at=new_at))
        db.commit()
        result = checks(build_data_health(db, now=NOW))
        assert result["session_history"]["retained"] == 0
        assert result["session_sync"]["total"] == 1
        assert result["session_sync"]["status"] == ("good" if new_at == NOW else "attention")
        assert db.query(RuntimeSession).count() == 2
        assert not db.dirty and not db.new and not db.deleted


@pytest.mark.parametrize("rule", [_history_rule(actor_id="other-session-sync"), _history_rule(runtime="hermes")])
def test_history_classification_matches_exact_actor_runtime_only(tmp_path, monkeypatch, rule):
    monkeypatch.setenv(HISTORY_SOURCES_ENV, json.dumps([rule]))
    factory = make_session_factory(tmp_path / "history-health.db")
    with factory() as db:
        _legacy_source(db)
        result = checks(build_data_health(db, now=NOW))
        assert result["session_history"]["retained"] == 0
        assert result["session_sync"]["items"][0]["state"] == "stale"
        assert result["session_sync"]["total"] == 1
        assert "session_history_config" not in result


@pytest.mark.parametrize("raw", [
    "", "invalid operator text", "null", "{}", '["not-a-rule"]',
    json.dumps([{"actor_id": "legacy-session-sync", "runtime": "codex"}]),
    json.dumps([_history_rule(extra="untrusted-extra-field")]),
    json.dumps([_history_rule(actor_id="../../actor")]),
    json.dumps([_history_rule(runtime="runtime with spaces")]),
    json.dumps([_history_rule(before="2026-08-11T12:00:00")]),
    json.dumps([_history_rule(before="2026-08-99T12:00:00Z")]),
    json.dumps([_history_rule(before=(NOW+dt.timedelta(days=1)).isoformat())]),
    json.dumps([_history_rule(), _history_rule()]),
    json.dumps([_history_rule(), _history_rule(actor_id="a"*65)]),
    json.dumps([_history_rule(), _history_rule(before=3)]),
    '[{"actor_id":"legacy-session-sync","runtime":"codex","runtime":"hermes","before":"2026-08-11T12:00:00Z"}]',
    " "*16385 + "[]", "["*2000 + "]"*2000,
    json.dumps([_history_rule(actor_id=f"source-{number}") for number in range(101)]),
])
def test_bad_history_configuration_never_hides_any_source_and_is_diagnostic(tmp_path, monkeypatch, raw):
    monkeypatch.setenv(HISTORY_SOURCES_ENV, raw)
    factory = make_session_factory(tmp_path / "history-health.db")
    with factory() as db:
        _legacy_source(db)
        result = checks(build_data_health(db, now=NOW))
        assert result["session_history"]["retained"] == 0
        assert result["session_sync"]["items"][0]["state"] == "stale"
        diagnostic = result["session_history_config"]
        assert diagnostic["status"] == "attention" and diagnostic["issue_count"] == 1
        assert diagnostic["items"][0]["state"] == "invalid_operator_configuration"
        assert "invalid operator text" not in json.dumps(result)
        assert "untrusted-extra-field" not in json.dumps(result)
        assert db.query(RuntimeSession).count() == 1


@pytest.mark.parametrize("configured", [None, "[]"])
def test_unconfigured_or_empty_history_rules_keep_default_current_checks(tmp_path, monkeypatch, configured):
    if configured is None:
        monkeypatch.delenv(HISTORY_SOURCES_ENV, raising=False)
    else:
        monkeypatch.setenv(HISTORY_SOURCES_ENV, configured)
    factory = make_session_factory(tmp_path / "history-health.db")
    with factory() as db:
        _legacy_source(db)
        result = checks(build_data_health(db, now=NOW))
        assert result["session_sync"]["total"] == 1
        assert result["session_history"]["retained"] == 0
        assert "session_history_config" not in result


def test_history_rule_never_reclassifies_token_reporting_source(tmp_path, monkeypatch):
    monkeypatch.setenv(HISTORY_SOURCES_ENV, json.dumps([_history_rule(before="2026-08-11T20:00:00+08:00")]))
    factory = make_session_factory(tmp_path / "history-health.db")
    with factory() as db:
        _legacy_source(db)
        db.add(TokenUsage(actor_id="legacy-session-sync", runtime="codex", date="2026-08-01",
                          input_tokens=4, output_tokens=2, updated_at=NOW-dt.timedelta(days=2)))
        db.commit()
        result = checks(build_data_health(db, now=NOW))
        assert result["session_history"]["retained"] == 1
        assert result["session_history"]["items"][0]["history_before"] == _history_rule()["before"]
        assert result["usage_sync"]["total"] == 1
        assert result["usage_sync"]["items"][0]["state"] == "stale"
