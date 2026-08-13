"""Read-only task board and task-thread web panel."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
import json
from pathlib import Path
from urllib.parse import unquote
from wsgiref.simple_server import make_server

import yaml

from core.protocol.org import validate_org
from core.protocol.task import ProtocolError, load_task


BOARD_STATES = ("queued", "doing", "handoff", "blocked", "done")
CSS = """
:root{color-scheme:dark;--bg:#0b1020;--panel:#121a2d;--line:#26324c;--text:#edf2ff;--muted:#91a0bd;--accent:#82aaff}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 20% 0,#1a2850 0,transparent 38%),var(--bg);color:var(--text);font:14px/1.5 Inter,ui-sans-serif,system-ui,sans-serif}
header{padding:28px 32px 18px;display:flex;align-items:end;justify-content:space-between}h1{margin:0;font-size:25px;letter-spacing:-.03em}.sub,.meta{color:var(--muted)}
.nav{display:flex;gap:9px}.nav a{color:var(--text);text-decoration:none;border:1px solid var(--line);border-radius:9px;padding:7px 10px}.nav a[aria-current=page]{border-color:var(--accent);color:var(--accent)}
.board{display:grid;grid-template-columns:repeat(5,minmax(210px,1fr));gap:14px;padding:12px 24px 32px;overflow:auto}.column{min-height:68vh;background:color-mix(in srgb,var(--panel) 86%,transparent);border:1px solid var(--line);border-radius:16px;padding:13px}.column h2{font-size:12px;text-transform:uppercase;letter-spacing:.12em;margin:2px 3px 13px;display:flex;justify-content:space-between}.count{color:var(--muted)}
.card{display:block;color:inherit;text-decoration:none;background:#182238;border:1px solid #2b3855;border-radius:12px;padding:13px;margin-bottom:10px;box-shadow:0 8px 22px #05081255}.card:hover{border-color:var(--accent);transform:translateY(-1px)}.task-id{font:11px ui-monospace,SFMono-Regular,monospace;color:var(--accent)}.title{font-weight:650;margin:6px 0 12px}.card-foot{display:flex;justify-content:space-between;color:var(--muted);font-size:12px}.pill{background:#26324b;border-radius:99px;padding:2px 7px}.relations{margin:0 0 10px;color:#b9c5dd;font-size:11px}.relations strong{color:#ffd580}.dependency-summary{background:var(--panel);border:1px solid var(--line);border-radius:13px;padding:14px;margin-bottom:18px}.dependency-summary ul{margin-bottom:0}
.overview{padding:12px 24px 44px;max-width:1180px}.overview-summary{display:flex;gap:14px;margin-bottom:18px}.stat{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:14px 18px;min-width:160px}.stat strong{display:block;font-size:22px}.roster{display:grid;gap:10px}.agent{display:grid;grid-template-columns:minmax(150px,1.2fr) minmax(150px,1fr) minmax(100px,.8fr) minmax(175px,1.2fr) minmax(240px,2fr);gap:14px;align-items:center;background:var(--panel);border:1px solid var(--line);border-radius:13px;padding:14px}.roster-head{border:0;background:transparent;border-radius:0;padding-block:0;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.08em}.agent-name{font-weight:750}.runtime,.activity{color:var(--muted)}.activity{white-space:nowrap}.presence{display:inline-flex;align-items:center;gap:7px}.dot{width:8px;height:8px;border-radius:50%;background:#69748a}.dot.online{background:#43d17d;box-shadow:0 0 0 4px #43d17d1c}.token-value{display:flex;justify-content:space-between;font-variant-numeric:tabular-nums}.token-track{height:7px;background:#0b1325;border-radius:99px;overflow:hidden;margin-top:7px}.token-fill{height:100%;background:linear-gradient(90deg,#597ef7,#36cfc9);border-radius:inherit}.source{font-size:11px;color:var(--muted);margin-top:5px}
.thread{max-width:820px;margin:0 auto;padding:12px 24px 48px}.back{color:var(--accent);text-decoration:none}.thread-head{margin:24px 0 30px}.event{display:grid;grid-template-columns:42px 1fr;gap:13px;position:relative;padding-bottom:22px}.event:before{content:'';position:absolute;left:20px;top:42px;bottom:0;width:1px;background:var(--line)}.event:last-child:before{display:none}.avatar{width:42px;height:42px;border-radius:50%;display:grid;place-items:center;background:linear-gradient(135deg,#597ef7,#9254de);font-weight:800}.bubble{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:13px 15px}.event-top{display:flex;justify-content:space-between;gap:12px}.who{font-weight:700}.receipt{margin-top:9px;padding:9px 11px;border-radius:9px;background:#0c1427;font-family:ui-monospace,SFMono-Regular,monospace;font-size:12px}.empty{color:var(--muted);padding:8px}
@media(max-width:900px){.board{grid-template-columns:repeat(5,260px)}header{padding-inline:24px}.agent{grid-template-columns:1fr 1fr}.roster-head{display:none}.token-cell{grid-column:1/-1}}
"""


def task_payload(root: Path) -> list[dict]:
    tasks = []
    for path in sorted((root / "tasks").glob("*.y*ml")):
        task = load_task(path)
        value = dict(task)
        value["last_receipt_at"] = task["chain"][-1]["at"] if task["chain"] else None
        tasks.append(value)
    by_id = {task["id"]: task for task in tasks}
    for task in tasks:
        task["blocked_by"] = [
            {
                "id": task_id,
                "title": by_id[task_id]["title"],
                "status": by_id[task_id]["status"],
                "kind": "blocks",
            }
            for task_id in task.get("depends_on", [])
            if task_id in by_id
        ]
        task["blocks"] = [
            {
                "id": candidate["id"],
                "title": candidate["title"],
                "status": candidate["status"],
                "kind": "blocks",
            }
            for candidate in tasks
            if task["id"] in candidate.get("depends_on", [])
        ]
        task["ready"] = task["status"] == "queued" and all(
            item["status"] == "done" for item in task["blocked_by"]
        )
    return tasks


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _nonnegative_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def overview_payload(root: Path, now: datetime | None = None) -> dict:
    """Merge the canonical roster with exporter snapshots."""
    try:
        org = yaml.safe_load((root / "org.yaml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ProtocolError(f"cannot read organization: {exc}") from exc
    validate_org(org)
    current = now or datetime.now().astimezone()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)

    metrics = {}
    for path in sorted((root / "metrics").glob("*.json")):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProtocolError(f"cannot read metrics snapshot {path}: {exc}") from exc
        if isinstance(item, dict) and isinstance(item.get("agent_id"), str):
            metrics[item["agent_id"]] = item

    agents = []
    for agent in org["agents"]:
        metric = metrics.get(agent["id"], {})
        raw_activity = metric.get("last_active_at")
        activity = raw_activity if isinstance(raw_activity, str) else None
        active = _parse_time(activity)
        age = current.astimezone(timezone.utc) - active.astimezone(timezone.utc) if active else None
        online = age is not None and -timedelta(minutes=5) <= age <= timedelta(minutes=15)
        today = metric.get("today") if isinstance(metric.get("today"), dict) else {}
        source = metric.get("source") if isinstance(metric.get("source"), dict) else {}
        source_kind = source.get("kind")
        agents.append({
            "id": agent["id"],
            "dept": agent["dept"],
            "runtime": agent["runtime"],
            "model": agent.get("model"),
            "node": agent["node"],
            "last_active_at": activity,
            "online_inferred": online,
            "online_window_minutes": 15,
            "today_tokens": _nonnegative_int(today.get("total_tokens")),
            "today_sessions": _nonnegative_int(today.get("sessions")),
            "metrics_source": source_kind if isinstance(source_kind, str) else "none",
        })
    return {
        "org": org["org"],
        "generated_at": current.isoformat(timespec="seconds"),
        "today_tokens": sum(item["today_tokens"] for item in agents),
        "online_agents": sum(1 for item in agents if item["online_inferred"]),
        "agents": agents,
    }


def _layout(title: str, content: str) -> bytes:
    return f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'><title>{escape(title)}</title><style>{CSS}</style></head><body>{content}</body></html>".encode()


def render_board(tasks: list[dict], *, ready_only: bool = False) -> bytes:
    if ready_only:
        tasks = [task for task in tasks if task.get("ready")]
    buckets = {state: [] for state in BOARD_STATES}
    for task in tasks:
        state = "done" if task["status"] == "cancelled" else task["status"]
        if state in buckets:
            buckets[state].append(task)
    columns = []
    for state in BOARD_STATES:
        cards = []
        for task in buckets[state]:
            relations = []
            if task.get("blocked_by"):
                relations.append(
                    f"<strong>Blocked by</strong> {', '.join(escape(item['id']) for item in task['blocked_by'])}"
                )
            if task.get("blocks"):
                relations.append(
                    f"<strong>Blocks</strong> {', '.join(escape(item['id']) for item in task['blocks'])}"
                )
            relation_html = (
                f"<div class='relations'>{' · '.join(relations)}</div>" if relations else ""
            )
            cards.append(
                f"<a class='card' href='/tasks/{escape(task['id'])}'><div class='task-id'>{escape(task['id'])}</div>"
                f"<div class='title'>{escape(task['title'])}</div>{relation_html}<div class='card-foot'><span class='pill'>{escape(task['holder'])}</span>"
                f"<span>{escape(task.get('dept') or '—')} · {escape(task.get('last_receipt_at') or '—')}</span></div></a>"
            )
        columns.append(f"<section class='column' data-state='{state}'><h2><span>{state}</span><span class='count'>{len(cards)}</span></h2>{''.join(cards) or '<div class=empty>No tasks</div>'}</section>")
    board_current = "" if ready_only else " aria-current='page'"
    ready_current = " aria-current='page'" if ready_only else ""
    nav = f"<nav class='nav'><a href='/'{board_current}>Board</a><a href='/?ready=1'{ready_current}>Ready work</a><a href='/overview'>Overview</a></nav>"
    heading = "Ready work" if ready_only else "Task board"
    subtitle = "Queued cards with every prerequisite done" if ready_only else "Live from the canonical file bus"
    return _layout("Retinue board", f"<header><div><h1>{heading}</h1><div class='sub'>{subtitle}</div></div>{nav}<div class='meta'>{len(tasks)} tasks</div></header><main class='board'>{''.join(columns)}</main>")


def _compact_tokens(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


def _display_activity(value: object) -> str:
    parsed = _parse_time(value)
    if parsed is None:
        return "No activity"
    return parsed.isoformat(sep=" ", timespec="minutes")


def render_overview(payload: dict) -> bytes:
    maximum = max((agent["today_tokens"] for agent in payload["agents"]), default=0)
    rows = []
    for agent in payload["agents"]:
        width = 0 if maximum == 0 else max(2, round(agent["today_tokens"] / maximum * 100))
        state = "online" if agent["online_inferred"] else "offline"
        runtime = agent["runtime"] + (f" · {agent['model']}" if agent.get("model") else "")
        exact = f"{agent['today_tokens']:,}"
        rows.append(
            f"<article class='agent' data-agent='{escape(agent['id'])}'>"
            f"<div><div class='agent-name'>{escape(agent['id'])}</div><div class='presence'><span class='dot {state}'></span>{state} (inferred)</div></div>"
            f"<div class='runtime'>{escape(runtime)}</div><div>{escape(agent['node'])}</div>"
            f"<time class='activity' datetime='{escape(agent.get('last_active_at') or '')}'>{escape(_display_activity(agent.get('last_active_at')))}</time>"
            f"<div class='token-cell'><div class='token-value'><span>Today</span><strong title='{exact} tokens'>{_compact_tokens(agent['today_tokens'])}</strong></div>"
            f"<div class='token-track'><div class='token-fill' style='width:{width}%'></div></div>"
            f"<div class='source'>{agent['today_sessions']} sessions · {escape(agent['metrics_source'])}</div></div></article>"
        )
    nav = "<nav class='nav'><a href='/'>Board</a><a href='/overview' aria-current='page'>Overview</a></nav>"
    summary = (
        f"<div class='overview-summary'><div class='stat'><strong>{len(payload['agents'])}</strong><span class='meta'>agents</span></div>"
        f"<div class='stat'><strong>{payload['online_agents']}</strong><span class='meta'>online inferred</span></div>"
        f"<div class='stat'><strong>{_compact_tokens(payload['today_tokens'])}</strong><span class='meta'>tokens today</span></div></div>"
    )
    labels = "<div class='agent roster-head' aria-hidden='true'><div>Agent / presence</div><div>Runtime / model</div><div>Node</div><div>Recent activity</div><div>Today token use</div></div>"
    content = f"<header><div><h1>Agent overview</h1><div class='sub'>{escape(payload['org'])} · roster and runtime activity</div></div>{nav}<div class='meta'>15 min online window</div></header><main class='overview'>{summary}<section class='roster'>{labels if rows else ''}{''.join(rows) or '<div class=empty>No agents</div>'}</section></main>"
    return _layout("Retinue overview", content)


def render_thread(task: dict) -> bytes:
    events = []
    for event in task["chain"]:
        old_status = event.get("from_status") or "—"
        new_status = event.get("to_status") or task["status"]
        old_holder = event.get("from_holder") or "—"
        new_holder = event.get("to_holder") or task["holder"]
        receipt = f"状态：{old_status} → {new_status}　持棒：{old_holder} → {new_holder}"
        payload = event.get("payload")
        attribution = payload.get("acted_on_behalf_of") if isinstance(payload, dict) else None
        if isinstance(attribution, dict):
            authority = attribution.get("authorising_identity")
            performer = attribution.get("performing_agent")
            if isinstance(authority, str) and isinstance(performer, str):
                receipt += f"　执行：{performer} 代表 {authority}"
        who = event["who"]
        events.append(f"<article class='event'><div class='avatar'>{escape(who[:1].upper())}</div><div class='bubble'><div class='event-top'><span class='who'>{escape(who)}</span><time>{escape(event['at'])}</time></div><div>{escape(event['did'])}</div><div class='receipt'>{escape(receipt)}</div></div></article>")
    relations = []
    for label, key in (("Blocked by", "blocked_by"), ("Blocks", "blocks")):
        items = task.get(key, [])
        if items:
            relations.append(
                f"<li><strong>{label}:</strong> "
                + ", ".join(
                    f"{escape(item['id'])} ({escape(item['status'])})" for item in items
                )
                + "</li>"
            )
    dependency_html = (
        f"<section class='dependency-summary'><h2>Dependencies</h2><ul>{''.join(relations)}</ul></section>"
        if relations
        else ""
    )
    content = f"<main class='thread'><a class='back' href='/'>← Board</a><section class='thread-head'><div class='task-id'>{escape(task['id'])}</div><h1>{escape(task['title'])}</h1><div class='meta'>{escape(task['status'])} · {escape(task['holder'])} · {escape(task.get('dept') or '—')}</div></section>{dependency_html}{''.join(events)}</main>"
    return _layout(task["title"], content)


class PanelApp:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    @staticmethod
    def _respond(start_response, status: str, body: bytes, content_type: str):
        start_response(status, [("Content-Type", content_type), ("Content-Length", str(len(body))), ("Cache-Control", "no-store")])
        return [body]

    def __call__(self, environ, start_response):
        if environ.get("REQUEST_METHOD") != "GET":
            return self._respond(start_response, "405 Method Not Allowed", b"Retinue M2 panel is read-only\n", "text/plain; charset=utf-8")
        path = unquote(environ.get("PATH_INFO", "/"))
        try:
            tasks = task_payload(self.root)
            if path == "/api/tasks":
                body = (json.dumps(tasks, ensure_ascii=False) + "\n").encode()
                return self._respond(start_response, "200 OK", body, "application/json; charset=utf-8")
            if path == "/api/tasks/ready":
                body = (
                    json.dumps(
                        [task for task in tasks if task.get("ready")],
                        ensure_ascii=False,
                    )
                    + "\n"
                ).encode()
                return self._respond(start_response, "200 OK", body, "application/json; charset=utf-8")
            if path == "/api/overview":
                body = (json.dumps(overview_payload(self.root), ensure_ascii=False) + "\n").encode()
                return self._respond(start_response, "200 OK", body, "application/json; charset=utf-8")
            if path.startswith("/api/tasks/"):
                task_id = path.rsplit("/", 1)[-1]
                task = next((item for item in tasks if item["id"] == task_id), None)
                if task is None:
                    raise FileNotFoundError
                body = (json.dumps(task, ensure_ascii=False) + "\n").encode()
                return self._respond(start_response, "200 OK", body, "application/json; charset=utf-8")
            if path.startswith("/tasks/"):
                task_id = path.rsplit("/", 1)[-1]
                task = next((item for item in tasks if item["id"] == task_id), None)
                if task is None:
                    raise FileNotFoundError
                return self._respond(start_response, "200 OK", render_thread(task), "text/html; charset=utf-8")
            if path == "/":
                ready_only = environ.get("QUERY_STRING", "") == "ready=1"
                return self._respond(start_response, "200 OK", render_board(tasks, ready_only=ready_only), "text/html; charset=utf-8")
            if path == "/overview":
                return self._respond(start_response, "200 OK", render_overview(overview_payload(self.root)), "text/html; charset=utf-8")
        except (FileNotFoundError, ProtocolError):
            pass
        return self._respond(start_response, "404 Not Found", b"Not found\n", "text/plain; charset=utf-8")


def serve(root: Path | str, host: str = "127.0.0.1", port: int = 8787) -> None:
    if not 0 < port < 65536:
        raise ProtocolError("panel port must be between 1 and 65535")
    with make_server(host, port, PanelApp(root)) as server:
        print(f"Retinue panel: http://{host}:{port}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
