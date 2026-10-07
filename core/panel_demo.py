"""Build a GitHub Pages demo from the real webui plus frozen API JSON."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

from fastapi.testclient import TestClient

from core.protocol.task import ProtocolError
from core.panel_collaboration_demo import seed_collaboration_demo
from core.panel_demo_locale import localize_demo_fixture
from core.panel_demo_clock import demo_clock
from core.static_demo import MARKER as STATIC_DEMO_MARKER
from server.app import create_app
from server.db import make_session_factory, utcnow
from server.main import DB_FILENAME
from server.seed import seed_demo


MARKER = ".retinue-panel-demo"
DEMO_TEMPLATE = "company"
DEMO_USER = "pm"
DEMO_PASSWORD = f"{DEMO_USER}-demo-2026"
# Pinned so lane counts in /api/summary stay stable across rebuilds.
DEMO_TODAY = "2026-08-31"


@contextmanager
def _synthetic_sources_only():
    """Capture seeded data without consulting the build host's deployment."""
    isolated_env = {key: value for key, value in os.environ.items()
                    if not key.startswith("RETINUE_")}
    # The actor scanner otherwise inspects Path.home(); optional overview
    # paths may already have been cached when the kingdom module was imported.
    with patch.dict(os.environ, isolated_env, clear=True), \
            patch("server.routers.actors.scan_local_runtimes", return_value=[]), \
            patch("server.kingdom._read_snapshot", return_value=None):
        yield


def demo_quota_payload(generated_at: str) -> dict:
    """Synthetic quota rows for the offline panel; no real account identifiers."""
    fetched = f"{DEMO_TODAY}T09:30:00+00:00"
    return {
        "generated_at": generated_at,
        "providers": [
            {
                "provider": "claude",
                "kind": "subscription",
                "status": "ok",
                "plan": "pro",
                "account_fp": "a1b2c3d4e5f6",
                "nodes": ["demo-node"],
                "windows": [
                    {
                        "key": "five_hour",
                        "label": "five_hour",
                        "period": "5h",
                        "used_percent": 42,
                        "used": None,
                        "limit": None,
                        "unit": "percent",
                        "resets_at": f"{DEMO_TODAY}T14:00:00+00:00",
                        "raw_reset": None,
                    }
                ],
                "balance": None,
                "fetched_at": fetched,
                "error": None,
                "stale": False,
            },
            *[
                {
                    "provider": provider, "kind": "subscription", "status": "ok",
                    "plan": plan, "account_fp": None, "nodes": ["demo-node"],
                    "windows": [{
                        "key": key, "label": key, "period": period,
                        "used_percent": percent, "used": None, "limit": None,
                        "unit": "percent", "resets_at": f"{DEMO_TODAY}T14:00:00+00:00",
                        "raw_reset": None,
                    }],
                    "balance": None, "fetched_at": fetched, "error": None, "stale": False,
                }
                for provider, plan, key, period, percent in [
                    ("codex", "plus", "five_hour", "5h", 78),
                    ("grok", "premium", "daily", "daily", 23),
                    ("cursor", "pro", "monthly", "monthly", 54),
                    ("kimi", "moderato", "weekly", "weekly", 17),
                ]
            ],
            {
                "provider": "moonshot",
                "kind": "api",
                "status": "ok",
                "plan": None,
                "account_fp": "f6e5d4c3b2a1",
                "nodes": ["demo-node"],
                "windows": [],
                "balance": {"amount": 128.5, "currency": "CNY"},
                "fetched_at": fetched,
                "error": None,
                "stale": False,
            },
        ],
    }


def route_filename(path: str) -> str:
    """Map a request path (with optional query) to a flat file name under api/."""
    cleaned = path.lstrip("/")
    if not cleaned.startswith("api/"):
        raise ValueError(f"not an API path: {path}")
    stem = cleaned[4:]
    stem = stem.replace("/", "__")
    # Percent-encoded query values must not become percent escapes in static
    # filenames: a file server decodes %2F as a path separator when fetched.
    stem = re.sub(r"[?&=%]", "_", stem)
    stem = re.sub(r"_+", "_", stem).strip("_")
    return f"{stem}.json"


def _paginate_tasks(client: TestClient) -> list[str]:
    captured: list[str] = []
    cursor: str | None = None
    while True:
        path = "/api/tasks?page_size=100"
        if cursor:
            path += f"&cursor={quote(cursor, safe='')}"
        captured.append(path)
        body = client.get(path).json()
        if not body.get("has_more"):
            break
        cursor = body.get("next_cursor")
        if not cursor:
            break
    return captured


def collect_api_paths(client: TestClient, today: str) -> list[str]:
    paths = [
        "/api/auth/me",
        "/api/login-config",
        f"/api/summary?today={today}",
        "/api/status",
        "/api/sessions?limit=24",
        "/api/sessions?limit=200",
        "/api/inbox?lane_limit=5",
        "/api/tasks",
        "/api/tasks/ready",
        "/api/actors",
        "/api/skills",
        "/api/nodes",
        "/api/knowledge",
        "/api/agent-discovery",
        "/api/data-catalog",
        "/api/metrics/throughput?days=14",
        "/api/metrics/summary?days=14",
        "/api/approvals?pending=true",
        "/api/pipeline-templates",
        "/api/todos/home",
        "/api/quota",
    ]
    # The shared operations period control must work without a live API too.
    for days in (1, 7, 30):
        for endpoint in ("summary", "throughput"):
            paths.append(f"/api/metrics/{endpoint}?days={days}&timezone=Asia%2FShanghai")
    paths.extend(_paginate_tasks(client))
    tasks_payload = client.get("/api/tasks").json()
    if isinstance(tasks_payload, dict):
        task_rows = tasks_payload.get("items") or []
    else:
        task_rows = tasks_payload
    task_ids = {row["id"] for row in task_rows}
    for task_id in sorted(task_ids):
        paths.append(f"/api/tasks/{task_id}")
        paths.append(f"/api/tasks/{task_id}/collaboration")
        paths.append(f"/api/tasks/{task_id}/context")
        paths.append(f"/api/approvals?task_id={task_id}")
        paths.append(f"/api/sessions?task_id={task_id}")
    session_rows = client.get("/api/sessions?limit=200").json()
    for row in session_rows:
        sid = row["id"]
        paths.append(f"/api/sessions/{sid}")
        paths.append(f"/api/sessions/{sid}/captures")
    return paths


def dump_api_snapshots(client: TestClient, api_dir: Path, today: str, *, language: str = "zh-CN") -> dict[str, str]:
    api_dir.mkdir(parents=True, exist_ok=True)
    index: dict[str, str] = {}
    for path in collect_api_paths(client, today):
        response = client.get(path)
        if response.status_code != 200:
            raise RuntimeError(f"{path} -> HTTP {response.status_code}")
        name = route_filename(path)
        target = api_dir / name
        payload = response.json()
        if path == "/api/quota":
            payload = demo_quota_payload(payload.get("generated_at") or f"{today}T09:42:00+00:00")
        if path.startswith("/api/auth/me") and isinstance(payload, dict):
            payload = {**payload, "role": "viewer", "readonly": True}
        if path.endswith("/collaboration") and isinstance(payload, dict):
            payload["can_delegate"] = False
            payload["can_manage_delegation_policy"] = False
            payload["delegation_reason"] = "公开演示为只读；此处展示合成的协作记录。"
            for recovery in payload.get("recovery", []):
                recovery.update(available=False, endpoint=None, reason="公开演示为只读。")
        if language == "en":
            # Only fixed synthetic seed data reaches this builder. This is not
            # a live translation layer and never rewrites canonical records.
            payload = localize_demo_fixture(payload)
        target.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8", newline="\n")
        index[path] = name
    (api_dir / "_index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
        newline="\n",
    )
    return index


def _seed_client(data_dir: Path, advance_clock) -> TestClient:
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / DB_FILENAME
    # The builder creates a fresh seed directory; avoid opening an extra pooled
    # migration connection which would keep its temporary database locked on Windows.
    factory = make_session_factory(db_path)
    with factory() as db:
        seed_demo(db, template=DEMO_TEMPLATE)
        seed_collaboration_demo(db, advance_clock)
        db.commit()
    site = {
        "label": "Retinue 公开演示",
        "demo_user": DEMO_USER,
        "entry_label": "进入演示",
        "footnote": "样本数据，只读浏览",
    }
    (data_dir / "site-config.json").write_text(
        json.dumps(site, ensure_ascii=False), encoding="utf-8", newline="\n"
    )
    app = create_app(factory, data_dir=data_dir)
    client = TestClient(app)
    login = client.post(
        "/api/auth/login",
        json={"username": DEMO_USER, "password": DEMO_PASSWORD},
    )
    if login.status_code != 200:
        raise RuntimeError(f"demo login failed: {login.status_code}")
    return client


def _run_webui_build(destination: Path, today: str, observed_at: str, language: str, install_dependencies: bool) -> None:
    repo = Path(__file__).resolve().parents[1]
    webui = repo / "webui"
    env = {
        **dict(**{k: v for k, v in __import__("os").environ.items()}),
        "VITE_DEMO_MODE": "1",
        "VITE_DEMO_TODAY": today,
        "VITE_DEMO_NOW": observed_at,
        "VITE_DEFAULT_LANGUAGE": language,
        "PANEL_DEMO_OUTDIR": str(destination.resolve()),
    }
    npm = "npm.cmd" if shutil.which("npm.cmd") else "npm"
    if install_dependencies:
        subprocess.run([npm, "ci", "--no-audit", "--no-fund"], cwd=webui, check=True, env=env)
    subprocess.run([npm, "run", "build"], cwd=webui, check=True, env=env)


def build_panel_demo(destination: Path | str, *, skip_npm: bool = False, language: str = "zh-CN", install_dependencies: bool = True) -> list[Path]:
    if language not in {"zh-CN", "en"}:
        raise ProtocolError("demo language must be zh-CN or en")
    dest = Path(destination)
    marker = dest / MARKER
    legacy = dest / STATIC_DEMO_MARKER
    if dest.exists() and not marker.is_file() and not legacy.is_file():
        raise ProtocolError(f"refusing to write panel demo into non-generated tree: {dest}")

    staging = dest.parent / f".{dest.name}-panel-staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    seed_dir = dest.parent / ".panel-demo-seed"
    if seed_dir.exists():
        shutil.rmtree(seed_dir)
    api_snap = staging / "_api_snap"
    with demo_clock(DEMO_TODAY) as advance_clock, _synthetic_sources_only():
        client = _seed_client(seed_dir, advance_clock)
        try:
            with client:
                route_count = len(dump_api_snapshots(client, api_snap, DEMO_TODAY, language=language))
                observed_at = utcnow().isoformat()
        finally:
            client.app.state.session_factory.kw["bind"].dispose()

    site_root = staging / "site"
    if skip_npm:
        site_root.mkdir(parents=True)
        (site_root / "index.html").write_text(
            "<!doctype html><title>panel demo (skip npm)</title>", encoding="utf-8", newline="\n"
        )
    else:
        _run_webui_build(site_root, DEMO_TODAY, observed_at, language, install_dependencies)

    for item in site_root.iterdir():
        target = staging / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)
    shutil.rmtree(site_root)
    # Vite injects LF into an index source that may be checked out as CRLF.
    # Normalize generated HTML so Windows builds do not publish mixed endings.
    for html in staging.rglob("*.html"):
        html.write_text(html.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    shutil.copytree(api_snap, staging / "api", dirs_exist_ok=True)
    shutil.rmtree(api_snap)

    (staging / MARKER).write_text("generated by scripts/build_panel_demo.py\n", encoding="utf-8", newline="\n")
    manifest = {
        "kind": "panel-demo",
        "template": DEMO_TEMPLATE,
        "demo_today": DEMO_TODAY,
        "observed_at": observed_at,
        "network_required": False,
        "api_routes": route_count,
        "language": language,
    }
    (staging / "build.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8", newline="\n")
    (staging / ".nojekyll").write_text("", encoding="utf-8", newline="\n")

    if dest.exists():
        shutil.rmtree(dest)
    shutil.move(str(staging), str(dest))
    if seed_dir.exists():
        shutil.rmtree(seed_dir, ignore_errors=True)

    return sorted(path for path in dest.rglob("*") if path.is_file())
