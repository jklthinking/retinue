import json
from pathlib import Path

import pytest

from core.panel_demo import MARKER, build_panel_demo, route_filename


def test_route_filename_maps_summary_query():
    assert route_filename("/api/summary?today=2026-08-31") == "summary_today_2026-08-31.json"
    name = route_filename("/api/metrics/summary?days=7&timezone=Asia%2FShanghai")
    assert name == "metrics_summary_days_7_timezone_Asia_2FShanghai.json"
    assert "%" not in name and "/" not in name


def test_panel_demo_api_snapshots_are_offline_and_marked(tmp_path):
    dest = tmp_path / "demo"
    build_panel_demo(dest, skip_npm=True)

    assert (dest / MARKER).is_file()
    assert not (dest.parent / ".panel-demo-seed").exists()
    manifest = json.loads((dest / "build.json").read_text(encoding="utf-8"))
    assert manifest["kind"] == "panel-demo"
    assert manifest["network_required"] is False
    assert manifest["observed_at"] == "2026-08-31T09:43:05+00:00"

    index = json.loads((dest / "api" / "_index.json").read_text(encoding="utf-8"))
    assert "/api/auth/me" in index
    account = json.loads((dest / "api" / index["/api/auth/me"]).read_text(encoding="utf-8"))
    assert account["role"] == "viewer" and account["readonly"] is True
    assert "/api/summary?today=2026-08-31" in index
    for days in (1, 7, 30):
        for endpoint in ("summary", "throughput"):
            assert f"/api/metrics/{endpoint}?days={days}&timezone=Asia%2FShanghai" in index
    actors = {row["id"]: row for row in json.loads(
        (dest / "api" / index["/api/actors"]).read_text(encoding="utf-8"))}
    usage = json.loads((dest / "api" / index[
        "/api/metrics/summary?days=7&timezone=Asia%2FShanghai"]).read_text(encoding="utf-8"))
    reported = {row["actor_id"]: row for row in usage["actors"]}
    for actor_id in ("analyst", "dev-assist", "copywriter"):
        assert reported[actor_id]["usage_available"]
        assert {row["runtime"] for row in reported[actor_id]["runtimes"]} == {actors[actor_id]["runtime"]}
    collaboration = [json.loads((dest / "api" / name).read_text(encoding="utf-8"))
                     for path, name in index.items() if path.endswith("/collaboration")]
    scenario = next(item for item in collaboration if len(item["delegations"]) == 2)
    assert scenario["generated_at"] == manifest["observed_at"]
    assert not any(item["source"] == "unassigned" for item in scenario["modules"])
    assert len(scenario["tasks"]) == 3
    assert {edge["delegated_by"] for edge in scenario["delegations"]} == {"analyst"}
    assert any(run["status"] == "succeeded" and run["attempt_id"] for run in scenario["runs"])
    finished = next(run for run in scenario["runs"] if run["status"] == "succeeded")
    assert finished["ended_at"] > finished["started_at"]
    assert len({run["started_at"] for run in scenario["runs"]}) == 3
    assert any(run.get("waiting", {}).get("kind") == "review"
               for run in scenario["runs"] if run.get("waiting"))
    assert scenario["can_delegate"] is False
    assert scenario["can_manage_delegation_policy"] is False
    assert all(not item["available"] for item in scenario["recovery"])
    assert scenario["root_task_id"].startswith("task-20260831-")

    board = (dest / "index.html").read_text(encoding="utf-8")
    assert "#0b1020" not in board
    assert "class='card'" not in board

    for path in dest.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        assert "http://" not in text
        assert "https://" not in text


def test_panel_demo_snapshots_are_repeatable_and_clock_is_restored(tmp_path):
    import server.db as database
    original_clock_module = database.dt
    first, second = tmp_path / "first" / "demo", tmp_path / "second" / "demo"
    build_panel_demo(first, skip_npm=True)
    build_panel_demo(second, skip_npm=True)
    assert database.dt is original_clock_module
    one = {path.name: path.read_bytes() for path in (first / "api").iterdir()}
    two = {path.name: path.read_bytes() for path in (second / "api").iterdir()}
    assert one == two


def test_panel_demo_does_not_read_host_runtime_or_deployment_sources(tmp_path, monkeypatch):
    import os
    import server.routers.actors as actors
    import server.kingdom as kingdom

    def reject_host_read(*args, **kwargs):
        pytest.fail("public demo attempted to read host runtime data")

    monkeypatch.setattr(actors, "scan_local_runtimes", reject_host_read)
    monkeypatch.setattr(kingdom, "_read_snapshot", reject_host_read)
    monkeypatch.setenv("RETINUE_KINGDOM_ROOT", str(tmp_path / "host-only"))
    monkeypatch.setenv("RETINUE_SESSION_HISTORY_SOURCES", "host-only-fixture")
    dest = tmp_path / "demo"
    build_panel_demo(dest, skip_npm=True)
    assert os.environ["RETINUE_SESSION_HISTORY_SOURCES"] == "host-only-fixture"
    assert actors.scan_local_runtimes is reject_host_read
    assert kingdom._read_snapshot is reject_host_read
    index = json.loads((dest / "api" / "_index.json").read_text(encoding="utf-8"))
    discovery = json.loads((dest / "api" / index["/api/agent-discovery"]).read_text(encoding="utf-8"))
    assert not any(row.get("local_detected") or row.get("path_hint")
                   for row in discovery["runtimes"])
    assert "host-only-fixture" not in "".join(
        path.read_text(encoding="utf-8") for path in (dest / "api").glob("*.json"))


def test_panel_demo_refuses_unmanaged_output(tmp_path):
    unmanaged = tmp_path / "foreign"
    unmanaged.mkdir()
    (unmanaged / "keep.txt").write_text("keep", encoding="utf-8")
    from core.protocol.task import ProtocolError

    with pytest.raises(ProtocolError, match="non-generated"):
        build_panel_demo(unmanaged)


def test_english_demo_preserves_evidence_and_translates_only_seed_copy(tmp_path):
    from core.panel_demo_locale import localize_demo_fixture

    dest = tmp_path / "demo-en"
    build_panel_demo(dest, skip_npm=True, language="en")
    manifest = json.loads((dest / "build.json").read_text(encoding="utf-8"))
    assert manifest["language"] == "en"
    assert manifest["observed_at"] == "2026-08-31T09:43:05+00:00"
    index = json.loads((dest / "api/_index.json").read_text(encoding="utf-8"))
    # Every visible fixture string in the English public edition is translated.
    # New seed copy must get a reviewed English counterpart before publication.
    import re
    for name in set(index.values()):
        assert not re.search(r"[\u3400-\u9fff]", (dest / "api" / name).read_text(encoding="utf-8"))
    scenarios = [json.loads((dest / "api" / name).read_text(encoding="utf-8"))
                 for path, name in index.items() if path.endswith("/collaboration")]
    scenario = next(item for item in scenarios if len(item["delegations"]) == 2)
    root = next(task for task in scenario["tasks"] if task["id"] == scenario["root_task_id"])
    assert root["title"] == "Collaboration demo: product launch plan"
    assert scenario["root_task_id"] == "task-20260831-009"
    assert {edge["delegated_by"] for edge in scenario["delegations"]} == {"analyst"}
    assert any(run["status"] == "succeeded" and run["attempt_id"] for run in scenario["runs"])
    assert any(run.get("waiting", {}).get("owner") == "pm"
               for run in scenario["runs"] if run.get("waiting"))
    assert not scenario["can_delegate"]
    original = {"id": "demo-worker", "note": "用户自己的任务正文"}
    assert localize_demo_fixture(original) == original
    assert localize_demo_fixture(original) is not original


def test_invalid_demo_language_does_not_touch_output(tmp_path):
    from core.protocol.task import ProtocolError

    dest = tmp_path / "foreign"
    dest.mkdir()
    with pytest.raises(ProtocolError, match="language"):
        build_panel_demo(dest, skip_npm=True, language="invalid")
    assert not list(dest.iterdir())
