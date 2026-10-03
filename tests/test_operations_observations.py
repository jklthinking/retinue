"""Snapshot projection time must not make historical usage or gateways current."""
from __future__ import annotations

import datetime as dt

from server import kingdom


def snapshot(node, *, age_days=0, age_minutes=0, readable=True, healthy=True):
    now = dt.datetime.now(dt.timezone.utc)
    observed = now - dt.timedelta(days=age_days, minutes=age_minutes)
    usage = {"today_input": 0, "today_output": 0, "today_tokens": 0,
             "today_sessions": 0, "week_tokens": 12,
             "daily": [{"day": observed.astimezone().date().isoformat(), "tokens": 0}] if readable else []}
    return {"schema_version": 1, "generated_at": observed.isoformat(), "node": {"id": node},
            "agents": [{"id": "worker-" + str(kingdom.NODES.index(node)), "node": node,
                        "gateway": {"healthy": healthy}, "usage": usage}], "tasks": {}}


def overview(monkeypatch, **kwargs):
    sources = {node: snapshot(node, **kwargs) for node in kingdom.NODES}
    monkeypatch.setattr(kingdom, "_read_snapshot", lambda node: sources[node])
    combined = kingdom._combined_overview()
    return sources, combined, kingdom._operations_overview(combined)


def test_projection_does_not_refresh_old_observation_or_gateway_health(monkeypatch):
    sources, combined, ops = overview(monkeypatch, age_days=3)
    assert combined["generated_at"] > sources[kingdom.NODES[0]]["generated_at"]
    assert all(row["observed_at"] == sources[row["id"]]["generated_at"] for row in ops["nodes"])
    for row in ops["agents"]:
        assert row["usage"]["available"] is True
        assert row["usage"]["recorded_today_tokens"] == 0
        assert row["usage"]["current_today_tokens"] is None
        assert row["snapshot_stale"] is True
        assert row["gateway"]["healthy"] is True
        assert row["gateway"]["current_process_healthy"] is None
        assert row["gateway"]["status_basis"] == "process_health"
    assert ops["totals"]["process_healthy_gateways"] == len(kingdom.NODES)
    assert ops["totals"]["current_process_healthy_gateways"] is None
    assert ops["totals"]["current_today_tokens"] is None


def test_missing_state_database_is_unknown_not_current_zero(monkeypatch):
    _, _, ops = overview(monkeypatch, readable=False)
    assert ops["usage_available"] is False
    assert ops["coverage"]["available_agents"] == 0
    assert ops["coverage"]["current_complete"] is False
    assert ops["totals"]["current_today_tokens"] is None
    assert all(row["usage"]["current_today_tokens"] is None for row in ops["agents"])


def test_recent_recorded_zero_and_offline_process_are_preserved(monkeypatch):
    _, _, ops = overview(monkeypatch, healthy=False)
    assert ops["coverage"]["current_complete"] is True
    assert ops["totals"]["current_today_tokens"] == 0
    assert ops["totals"]["current_process_healthy_gateways"] == 0
    for row in ops["agents"]:
        assert row["usage"]["time_basis"] == "session_started_day"
        assert row["usage"]["completeness"] == "unknown"
        assert "cumulative" in row["usage"]["accounting"]


def test_fresh_observation_from_a_previous_statistical_day_is_not_today(monkeypatch):
    sources = {node: snapshot(node) for node in kingdom.NODES}
    old_day = (dt.datetime.now().date() - dt.timedelta(days=1)).isoformat()
    for source in sources.values():
        source["agents"][0]["usage"]["daily"] = [{"day": old_day, "tokens": 8}]
        source["agents"][0]["usage"]["today_input"] = 8
        source["agents"][0]["usage"]["today_tokens"] = 8
    monkeypatch.setattr(kingdom, "_read_snapshot", lambda node: sources[node])
    ops = kingdom._operations_overview(kingdom._combined_overview())
    assert ops["totals"]["recorded_today_tokens"] == 16
    assert ops["totals"]["current_today_tokens"] is None
    assert all(row["snapshot_stale"] is False and row["usage"]["window_current"] is False for row in ops["agents"])


def test_same_day_observation_expires_after_two_metadata_collection_cycles(monkeypatch):
    _, _, ops = overview(monkeypatch, age_minutes=31)
    assert ops["stale_after_seconds"] == 30 * 60
    assert ops["totals"]["current_process_healthy_gateways"] is None
    assert ops["totals"]["current_today_tokens"] is None
    assert all(row["stale"] for row in ops["nodes"])


def test_future_observation_cannot_claim_current_process_state(monkeypatch):
    _, _, ops = overview(monkeypatch, age_minutes=-10)
    assert all(row["clock_skew"] and row["stale"] for row in ops["nodes"])
    assert ops["totals"]["current_process_healthy_gateways"] is None
