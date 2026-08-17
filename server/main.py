"""Retinue Server CLI: serve, migrate, init-admin, seed-demo, import-yaml.

Examples:
    python -m server.main --data-dir ./retinue-server-data migrate
    python -m server.main --data-dir ./retinue-server-data init-admin --username queen
    python -m server.main --data-dir ./retinue-server-data seed-demo
    python -m server.main --data-dir ./retinue-server-data snapshot
    python -m server.main --data-dir ./retinue-server-data serve --port 9219
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from core.cli.output import configure_output_streams

from .db import Actor, Task, TaskEvent, User, make_session_factory, migrate_database
from .security import hash_password

DB_FILENAME = "retinue.db"
STATIC_DIR = Path(__file__).parent / "static"


def _factory(data_dir: str):
    data = Path(data_dir)
    data.mkdir(parents=True, exist_ok=True)
    return make_session_factory(data / DB_FILENAME)


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .app import create_app

    app = create_app(
        _factory(args.data_dir), static_dir=STATIC_DIR, data_dir=Path(args.data_dir)
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


def cmd_init_admin(args: argparse.Namespace) -> int:
    factory = _factory(args.data_dir)
    password = args.password or getpass.getpass("管理员密码: ")
    if len(password) < 8:
        print("密码至少 8 位", file=sys.stderr)
        return 1
    with factory() as db:
        from sqlalchemy import select

        if db.execute(select(User).where(User.username == args.username)).scalar():
            print(f"用户已存在: {args.username}", file=sys.stderr)
            return 1
        actor_id = args.actor or args.username
        if db.get(Actor, actor_id) is None:
            db.add(Actor(id=actor_id, kind="human", display_name=args.display_name or args.username))
        db.add(
            User(
                username=args.username,
                password_hash=hash_password(password),
                role="admin",
                display_name=args.display_name or args.username,
                actor_id=actor_id,
            )
        )
        db.commit()
    print(f"管理员已创建: {args.username} (actor: {actor_id})")
    return 0


def cmd_seed_demo(args: argparse.Namespace) -> int:
    from .seed import seed_demo

    factory = _factory(args.data_dir)
    with factory() as db:
        created = seed_demo(db, template=args.template)
        db.commit()
    print(
        f"演示数据就绪({args.template}): "
        f"{created['actors']} 个执行者, {created['tasks']} 张任务卡"
    )
    return 0


def cmd_migrate(args: argparse.Namespace) -> int:
    data = Path(args.data_dir)
    data.mkdir(parents=True, exist_ok=True)
    result = migrate_database(data / DB_FILENAME)
    if result.from_version == result.to_version:
        detail = "no changes"
    else:
        detail = "schema upgraded"
    print(
        f"Database migration: from version {result.from_version} "
        f"to version {result.to_version} ({detail})."
    )
    return 0


def cmd_issue_token(args: argparse.Namespace) -> int:
    from .db import ApiToken
    from .security import hash_token, new_token

    factory = _factory(args.data_dir)
    with factory() as db:
        actor = db.get(Actor, args.actor)
        if actor is None:
            print(f"未知执行者: {args.actor}(先在管理台或 seed 中创建)", file=sys.stderr)
            return 1
        token = new_token("rtn")
        db.add(ApiToken(token_hash=hash_token(token), actor_id=args.actor, label=args.label))
        db.commit()
    print(token)
    return 0


def cmd_issue_node_token(args: argparse.Namespace) -> int:
    from .db import Node, NodeToken, utcnow
    from .security import hash_token, new_token

    factory = _factory(args.data_dir)
    token = new_token("rnn")
    with factory() as db:
        node = db.get(Node, args.node)
        if node is not None and node.membership_status == "retired":
            print(
                f"节点已退役: {args.node}(请先通过管理 API 明确重新准入)",
                file=sys.stderr,
            )
            return 1
        if node is None:
            db.add(
                Node(
                    id=args.node,
                    label=args.node,
                    membership_status="admitted",
                    admitted_by=args.admitted_by,
                    admitted_at=utcnow(),
                )
            )
        db.add(
            NodeToken(
                token_hash=hash_token(token),
                node_id=args.node,
                label=args.label,
            )
        )
        db.commit()
    output = Path(args.output).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(token + "\n", encoding="utf-8")
    output.chmod(0o600)
    print(f"节点令牌已写入: {output} ({args.node})")
    return 0


def cmd_push_usage(args: argparse.Namespace) -> int:
    from .push_usage import push_usage

    token = args.token or Path(args.token_file).read_text(encoding="utf-8").strip()
    pushed = push_usage(
        runtime=args.runtime,
        source=args.source,
        actor_id=args.actor,
        url=args.url,
        token=token,
    )
    print(f"已推送 {pushed} 天用量: {args.actor} ({args.runtime})")
    return 0


def cmd_sync_sessions(args: argparse.Namespace) -> int:
    from .push_sessions import push_sessions

    token = args.token or Path(args.token_file).read_text(encoding="utf-8").strip()
    result = push_sessions(
        runtime=args.runtime,
        source=args.source,
        actor_id=args.actor,
        url=args.url,
        token=token,
        privacy=args.privacy,
        limit=args.limit,
        max_messages=args.max_messages,
    )
    print(
        f"会话同步完成({args.privacy}): 新增 {result['created']}, "
        f"更新 {result['updated']}, 未变化 {result['unchanged']}"
    )
    return 0




def cmd_probe(args: argparse.Namespace) -> int:
    from .probe import collect, push

    token = args.token or Path(args.token_file).read_text(encoding="utf-8").strip()
    payload = collect(args.node, args.label, args.services.split(",") if args.services else [])
    push(args.url, token, payload)
    print(f"节点心跳已上报: {args.node} ({payload['hostname']})")
    return 0


def cmd_probe_runtimes(args: argparse.Namespace) -> int:
    from .runtime_probe import collect, push

    token = args.token or Path(args.token_file).read_text(encoding="utf-8").strip()
    payload = collect(args.node)
    push(args.url, token, payload)
    print(f"Runtime inventory reported: {args.node} ({len(payload['runtimes'])} available)")
    return 0

def cmd_mcp(args: argparse.Namespace) -> int:
    from .mcp_bridge import main as mcp_main

    mcp_main()
    return 0



def cmd_snapshot(args: argparse.Namespace) -> int:
    """Write a deterministic static board snapshot under ``<data-dir>/snapshots/``."""
    from .snapshot import write_board_snapshot

    factory = _factory(args.data_dir)
    with factory() as db:
        paths = write_board_snapshot(Path(args.data_dir), db)
    print(f"snapshot written: {paths['json']}")
    return 0


def cmd_import_yaml(args: argparse.Namespace) -> int:
    """Import legacy file-bus task cards (protocol v0.2 YAML) into the database."""
    from core.protocol.task import lint_path, load_task

    factory = _factory(args.data_dir)
    directory = Path(args.tasks_dir)
    problems = [(p, e) for p, e in lint_path(directory) if e]
    if problems:
        for path, error in problems:
            print(f"跳过 {path.name}: {error}", file=sys.stderr)
    imported = 0
    with factory() as db:
        for path in sorted(directory.glob("*.yaml")):
            if any(p == path for p, _ in problems):
                continue
            card = load_task(path)
            if db.get(Task, card["id"]):
                print(f"已存在,跳过: {card['id']}", file=sys.stderr)
                continue
            for who in {card["created_by"], card["holder"]}:
                if db.get(Actor, who) is None:
                    db.add(Actor(id=who, kind="agent", display_name=who))
            task = Task(
                id=card["id"],
                title=card["title"],
                created_by=card["created_by"],
                dept=card.get("dept"),
                priority=card.get("priority", "none"),
                status=card["status"],
                holder=card["holder"],
                blocked_reason=card.get("blocked_reason"),
                next_holder=card.get("next"),
                acceptance_json=json.dumps(card.get("acceptance", [])),
                refs_json=json.dumps(card.get("refs", [])),
                progress=card.get("progress", 0),
            )
            for seq, evt in enumerate(card["chain"], start=1):
                task.events.append(
                    TaskEvent(
                        seq=seq,
                        who=evt["who"],
                        did=evt["did"],
                        at=evt["at"],
                        from_status=evt.get("from_status"),
                        to_status=evt.get("to_status"),
                        from_holder=evt.get("from_holder"),
                        to_holder=evt.get("to_holder"),
                        payload_json=json.dumps(
                            evt.get("payload", {}),
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                    )
                )
            db.add(task)
            imported += 1
        db.commit()
    print(f"导入完成: {imported} 张任务卡")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="retinue-server")
    parser.add_argument("--data-dir", default="./retinue-data", help="数据目录(含 SQLite 库)")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="启动 API 与 Web 面板")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=9219)
    serve.set_defaults(func=cmd_serve)

    init_admin = sub.add_parser("init-admin", help="创建管理员账号")
    init_admin.add_argument("--username", required=True)
    init_admin.add_argument("--password", help="不传则交互输入")
    init_admin.add_argument("--display-name", default="")
    init_admin.add_argument("--actor", help="绑定的执行者 slug,默认同用户名")
    init_admin.set_defaults(func=cmd_init_admin)

    seed = sub.add_parser(
        "seed-demo",
        help=(
            "演示数据(writing=写作工作室, teacher=普通老师, "
            "edu=教培机构, company=普通公司)"
        ),
    )
    seed.add_argument("--template", choices=("writing", "teacher", "edu", "company"), default="writing")
    seed.set_defaults(func=cmd_seed_demo)

    migrate = sub.add_parser("migrate", help="显式升级服务器数据库结构(幂等)")
    migrate.set_defaults(func=cmd_migrate)

    issue = sub.add_parser("issue-token", help="为执行者签发 API/MCP 令牌(仅打印一次)")
    issue.add_argument("--actor", required=True)
    issue.add_argument("--label", default="cli-issued")
    issue.set_defaults(func=cmd_issue_token)

    node_issue = sub.add_parser("issue-node-token", help="签发仅可上报心跳的节点令牌")
    node_issue.add_argument("--node", required=True)
    node_issue.add_argument("--label", default="node-probe")
    node_issue.add_argument("--output", required=True)
    node_issue.add_argument(
        "--admitted-by",
        default="local-admin-cli",
        help="记录本次节点准入决定的本地操作员标识",
    )
    node_issue.set_defaults(func=cmd_issue_node_token)

    push = sub.add_parser("push-usage", help="推送脱敏用量(仅 token 计数)到服务器")
    push.add_argument("--runtime", choices=("claude-code", "codex"), required=True)
    push.add_argument("--source", required=True, help="转录目录,如 ~/.claude/projects")
    push.add_argument("--actor", required=True)
    push.add_argument("--url", default="http://127.0.0.1:9219")
    push.add_argument("--token")
    push.add_argument("--token-file")
    push.set_defaults(func=cmd_push_usage)

    sessions = sub.add_parser(
        "sync-sessions", help="只读同步本机 Agent 会话索引、摘要或最近消息"
    )
    sessions.add_argument("--runtime", choices=("claude-code", "codex"), required=True)
    sessions.add_argument("--source", required=True, help="运行时会话目录")
    sessions.add_argument("--actor", required=True)
    sessions.add_argument("--url", default="http://127.0.0.1:9219")
    credentials = sessions.add_mutually_exclusive_group(required=True)
    credentials.add_argument("--token")
    credentials.add_argument("--token-file")
    sessions.add_argument(
        "--privacy", choices=("metadata", "summary", "full"), default="metadata"
    )
    sessions.add_argument("--limit", type=int, default=50)
    sessions.add_argument("--max-messages", type=int, default=40)
    sessions.set_defaults(func=cmd_sync_sessions)


    probe = sub.add_parser("probe", help="上报本机节点健康心跳")
    probe.add_argument("--node", required=True)
    probe.add_argument("--label", default="")
    probe.add_argument("--services", default="", help="逗号分隔的 systemd unit 允许清单")
    probe.add_argument("--url", default="http://127.0.0.1:9219")
    probe.add_argument("--token")
    probe.add_argument("--token-file")
    probe.set_defaults(func=cmd_probe)

    runtime_probe = sub.add_parser(
        "probe-runtimes",
        help="Report available Agent CLIs found on PATH or in conventional install directories",
    )
    runtime_probe.add_argument("--node", required=True)
    runtime_probe.add_argument("--url", default="http://127.0.0.1:9219")
    runtime_credentials = runtime_probe.add_mutually_exclusive_group(required=True)
    runtime_credentials.add_argument("--token")
    runtime_credentials.add_argument("--token-file")
    runtime_probe.set_defaults(func=cmd_probe_runtimes)
    mcp = sub.add_parser("mcp", help="启动 MCP stdio 桥(读 RETINUE_SERVER_URL/RETINUE_TOKEN)")
    mcp.set_defaults(func=cmd_mcp)

    imp = sub.add_parser("import-yaml", help="导入文件总线 YAML 任务卡")
    imp.add_argument("tasks_dir")
    imp.set_defaults(func=cmd_import_yaml)

    snap = sub.add_parser(
        "snapshot",
        help="写出只读板面静态快照(JSON 与 HTML,不改库)",
    )
    snap.set_defaults(func=cmd_snapshot)

    return parser


def main(argv: list[str] | None = None) -> int:
    configure_output_streams()
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
