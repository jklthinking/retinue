"""Retinue command-line entry point."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import yaml

from adapters.im.feishu import FeishuAdapter, emit_if_configured, listen, normalize_event
from core.cli.output import configure_output_streams
from core.daemon import TaskDaemon
from core.panel import serve as serve_panel
from core.protocol.org import initialize
from core.protocol.task import (
    PRIORITIES,
    ProtocolError,
    add_dependency,
    audit_task_card,
    create_task,
    lint_path,
    load_task,
    render_receipt,
    ready_tasks,
    remove_dependency,
    update_task,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="retinue")
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="create org.yaml and data directories")
    init.add_argument("path", nargs="?", default=".")
    init.add_argument("--org", required=True)

    scan = commands.add_parser(
        "scan",
        help="report the AI agents present on this machine (offline, no account)",
    )
    scan.add_argument(
        "--json", action="store_true", help="print the report as JSON instead of text"
    )

    task = commands.add_parser("task", help="manage task cards")
    task_commands = task.add_subparsers(dest="task_command", required=True)

    new = task_commands.add_parser("new", help="create a queued task")
    new.add_argument("directory", type=Path)
    new.add_argument("--id", required=True)
    new.add_argument("--title", required=True)
    new.add_argument("--created-by", required=True)
    new.add_argument("--holder", required=True)
    new.add_argument("--dept")
    new.add_argument("--priority", choices=PRIORITIES, default="none")
    new.add_argument("--acceptance", action="append", default=[])
    new.add_argument("--depends-on", action="append", default=[])
    new.add_argument("--note", default="task created")
    new.add_argument("--at")

    update = task_commands.add_parser("update", help="update a task card")
    update.add_argument("file", type=Path)
    update.add_argument("--status")
    update.add_argument("--holder")
    update.add_argument("--dept")
    update.add_argument("--blocked-reason")
    update.add_argument("--next", dest="next_holder")
    update.add_argument("--priority", choices=PRIORITIES)
    update.add_argument("--acceptance", action="append")
    update.add_argument("--ref", action="append", default=[])
    update.add_argument("--note")
    update.add_argument("--who")
    update.add_argument("--at")
    update.add_argument("--progress", type=int)

    show = task_commands.add_parser("show", help="print a task card")
    show.add_argument("file", type=Path)

    audit = task_commands.add_parser(
        "audit", help="fold a task chain and report row drift"
    )
    audit.add_argument("file", type=Path)

    lint = task_commands.add_parser("lint", help="validate task cards")
    lint.add_argument("path", type=Path)

    receipt = commands.add_parser("receipt", help="render the latest IM receipt")
    receipt.add_argument("file", type=Path)

    ready = task_commands.add_parser(
        "ready", help="list queued cards whose prerequisites are done"
    )
    ready.add_argument("directory", type=Path)
    ready.add_argument("--holder")

    depend = task_commands.add_parser("depend", help="add a card prerequisite")
    depend.add_argument("file", type=Path)
    depend.add_argument("--on", dest="prerequisite_id", required=True)
    depend.add_argument("--note", default="dependency added")
    depend.add_argument("--who")

    undepend = task_commands.add_parser("undepend", help="remove a card prerequisite")
    undepend.add_argument("file", type=Path)
    undepend.add_argument("--on", dest="prerequisite_id", required=True)
    undepend.add_argument("--note", default="dependency removed")
    undepend.add_argument("--who")

    daemon = commands.add_parser("daemon", help="watch task cards for local claims")
    daemon.add_argument("root", type=Path)
    daemon.add_argument("--node", required=True)
    daemon.add_argument("--poll-interval", type=float, default=1.0)
    daemon.add_argument("--once", action="store_true")

    feishu = commands.add_parser("feishu", help="send receipts or receive Feishu events")
    feishu_commands = feishu.add_subparsers(dest="feishu_command", required=True)
    feishu_emit = feishu_commands.add_parser("emit", help="emit the latest task receipt")
    feishu_emit.add_argument("root", type=Path)
    feishu_emit.add_argument("file", type=Path)
    feishu_receive = feishu_commands.add_parser("receive", help="handle one event JSON from stdin")
    feishu_receive.add_argument("root", type=Path)
    feishu_listen = feishu_commands.add_parser("listen", help="consume long-connection NDJSON events")
    feishu_listen.add_argument("root", type=Path)
    feishu_listen.add_argument("--event-command")

    panel = commands.add_parser("panel", help="serve the read-only task panel")
    panel.add_argument("root", type=Path)
    panel.add_argument("--host", default="127.0.0.1")
    panel.add_argument("--port", type=int, default=8787)

    mcp = commands.add_parser("mcp", help="serve task tools over MCP stdio")
    mcp.add_argument("root", type=Path)
    mcp.add_argument(
        "--agent",
        help="agent identity (or set RETINUE_AGENT_ID); required for write tools",
    )

    export = commands.add_parser("export", help="export runtime metrics into the workspace")
    exporters = export.add_subparsers(dest="exporter", required=True)
    claude_code = exporters.add_parser(
        "claude-code", help="read Claude Code transcripts without modifying them"
    )
    claude_code.add_argument("root", type=Path)
    claude_code.add_argument("--agent", default="claude-code")
    claude_code.add_argument(
        "--source", type=Path, default=Path.home() / ".claude" / "projects"
    )
    claude_code.add_argument("--timezone")
    codex = exporters.add_parser(
        "codex", help="read Codex sessions without modifying them"
    )
    codex.add_argument("root", type=Path)
    codex.add_argument("--agent", default="codex")
    codex.add_argument(
        "--source", type=Path, default=Path.home() / ".codex" / "sessions"
    )
    codex.add_argument("--timezone")

    demo = commands.add_parser("demo", help="seed and serve a one-node sample fleet")
    demo.add_argument("path", nargs="?", type=Path, default=Path("retinue-demo"))
    demo.add_argument("--seed", type=int, default=42)
    demo.add_argument("--host", default="127.0.0.1")
    demo.add_argument("--port", type=int, default=8787)
    demo.add_argument("--no-serve", action="store_true", help="seed the workspace and exit")
    return parser


def run(args: argparse.Namespace) -> int:
    if args.command == "init":
        print(initialize(args.path, args.org))
    elif args.command == "scan":
        from core.scan import render_json, render_text, scan_machine

        report = scan_machine()
        print(render_json(report) if args.json else render_text(report))
    elif args.command == "daemon":
        daemon = TaskDaemon(args.root, args.node)
        if args.once:
            print(f"triggered: {daemon.scan_once()}")
        else:
            daemon.serve(args.poll_interval)
    elif args.command == "feishu":
        adapter = FeishuAdapter(args.root)
        if args.feishu_command == "emit":
            print(adapter.emit(args.file))
        elif args.feishu_command == "receive":
            print(adapter.receive(normalize_event(yaml.safe_load(sys.stdin.read()))))
        else:
            listen(adapter, args.event_command)
    elif args.command == "panel":
        serve_panel(args.root, args.host, args.port)
    elif args.command == "mcp":
        from core.mcp_server import serve as serve_mcp

        serve_mcp(args.root, args.agent)
    elif args.command == "export":
        if args.exporter == "claude-code":
            from adapters.exporters.claude_code import export_metrics

            result = export_metrics(
                args.source,
                args.root / "metrics" / f"{args.agent}.json",
                agent_id=args.agent,
                timezone_name=args.timezone,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.exporter == "codex":
            from adapters.exporters.codex import export_metrics

            result = export_metrics(
                args.source,
                args.root / "metrics" / f"{args.agent}.json",
                agent_id=args.agent,
                timezone_name=args.timezone,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "demo":
        from core.demo import seed_sample

        root = seed_sample(args.path, seed=args.seed)
        print(f"Sample workspace: {root}")
        print(f"Overview: http://{args.host}:{args.port}/overview")
        if not args.no_serve:
            serve_panel(root, args.host, args.port)
    elif args.command == "receipt":
        print(render_receipt(load_task(args.file)))
    elif args.task_command == "new":
        path = create_task(
            args.directory,
            task_id=args.id,
            title=args.title,
            created_by=args.created_by,
            holder=args.holder,
            dept=args.dept,
            priority=args.priority,
            acceptance=args.acceptance,
            depends_on=args.depends_on,
            note=args.note,
            at=args.at,
        )
        print(path)
        emit_if_configured(path)
    elif args.task_command == "update":
        updated = update_task(
            args.file,
            status=args.status,
            holder=args.holder,
            dept=args.dept,
            blocked_reason=args.blocked_reason,
            next_holder=args.next_holder,
            priority=args.priority,
            acceptance=args.acceptance,
            refs=args.ref,
            note=args.note,
            who=args.who,
            at=args.at,
            progress=args.progress,
        )
        print(yaml.safe_dump(updated, sort_keys=False, allow_unicode=True), end="")
        emit_if_configured(args.file)
    elif args.task_command == "show":
        print(yaml.safe_dump(load_task(args.file), sort_keys=False, allow_unicode=True), end="")
    elif args.task_command == "audit":
        report = audit_task_card(load_task(args.file))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["in_sync"] else 1
    elif args.task_command == "lint":
        results = lint_path(args.path)
        failed = False
        for path, error in results:
            if error:
                failed = True
                print(f"FAIL {path}: {error}")
            else:
                print(f"OK {path}")
        return 1 if failed else 0
    elif args.task_command == "ready":
        cards = ready_tasks(args.directory)
        if args.holder:
            cards = [card for card in cards if card["holder"] == args.holder]
        print(yaml.safe_dump(cards, sort_keys=False, allow_unicode=True), end="")
    elif args.task_command == "depend":
        updated = add_dependency(
            args.file,
            args.prerequisite_id,
            note=args.note,
            who=args.who,
        )
        print(yaml.safe_dump(updated, sort_keys=False, allow_unicode=True), end="")
    elif args.task_command == "undepend":
        updated = remove_dependency(
            args.file,
            args.prerequisite_id,
            note=args.note,
            who=args.who,
        )
        print(yaml.safe_dump(updated, sort_keys=False, allow_unicode=True), end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    configure_output_streams()
    try:
        return run(_parser().parse_args(argv))
    except ProtocolError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
