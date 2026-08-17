"""Retinue node agent CLI — the three node duties without the server stack.

Subcommands:
    heartbeat      report an infrastructure health heartbeat
    runtimes       report which agent CLIs exist (basename + availability only)
    sync-sessions  sync the privacy-scoped session index (read-only sources)
    whoami         print what this node would send, without sending anything
    enroll         render (default) or install the schedule for the selected duties

Configuration comes from arguments or the environment (arguments win):

    RETINUE_SERVER_URL         server base URL (default: http://127.0.0.1:9219)
    RETINUE_NODE_ID            this node's id (no default)
    RETINUE_NODE_TOKEN_FILE    path to the node-token file (no default)
    RETINUE_ACTOR_TOKEN_FILE   path to the actor-token file (no default)
    RETINUE_RUNTIME_PINS_FILE  path to the node-local runtime pin file
                               (default: ~/.config/retinue/runtime-pins.json)

The two token kinds are not interchangeable: the node token is a
heartbeat-only credential bound to one infrastructure node, while session
sync is attributed to an actor and must use an actor API token.

Nothing here imports the ``server`` package; the duties run on the standard
library plus ``adapters``.

Examples:
    retinue-node whoami --node workstation --token-file ./node-token
    retinue-node heartbeat --node workstation --url http://127.0.0.1:9219 \
        --token-file ./node-token
    retinue-node sync-sessions --runtime codex --source ./sessions \
        --actor agent-1 --actor-token-file ./actor-token
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from core.cli.output import configure_output_streams

ENV_URL = "RETINUE_SERVER_URL"
ENV_NODE = "RETINUE_NODE_ID"
ENV_TOKEN_FILE = "RETINUE_NODE_TOKEN_FILE"
ENV_ACTOR_TOKEN_FILE = "RETINUE_ACTOR_TOKEN_FILE"
ENV_PACKAGE_PATH = "RETINUE_PACKAGE_PATH"
DEFAULT_URL = "http://127.0.0.1:9219"


def _resolve_url(args: argparse.Namespace) -> str:
    return args.url or os.environ.get(ENV_URL) or DEFAULT_URL


def _resolve_node(args: argparse.Namespace) -> str:
    node = args.node or os.environ.get(ENV_NODE)
    if not node:
        raise SystemExit(f"节点 id 必填: 传 --node 或设置 {ENV_NODE}")
    return node


def _resolve_token_file(args: argparse.Namespace) -> str:
    token_file = args.token_file or os.environ.get(ENV_TOKEN_FILE)
    if not token_file:
        raise SystemExit(f"节点令牌文件必填: 传 --token-file 或设置 {ENV_TOKEN_FILE}")
    return token_file


def _resolve_actor_token_file(args: argparse.Namespace) -> str:
    """Session sync is attributed to an actor, so it authenticates with an
    actor API token, supplied separately from the heartbeat-only node
token.  ``--token-file`` remains accepted as a fallback for a manually
    supplied actor token file."""
    token_file = getattr(args, "actor_token_file", None) or os.environ.get(
        ENV_ACTOR_TOKEN_FILE
    )
    if token_file:
        return token_file
    return _resolve_token_file(args)


def _read_token(args: argparse.Namespace) -> str:
    return Path(_resolve_token_file(args)).expanduser().read_text(encoding="utf-8").strip()


def _read_actor_token(args: argparse.Namespace) -> str:
    token_file = _resolve_actor_token_file(args)
    return Path(token_file).expanduser().read_text(encoding="utf-8").strip()


def _services(args: argparse.Namespace) -> list[str]:
    return args.services.split(",") if args.services else []


def cmd_heartbeat(args: argparse.Namespace) -> int:
    from . import probe

    node = _resolve_node(args)
    url = _resolve_url(args)
    payload = probe.collect(node, args.label, _services(args))
    probe.push(url, _read_token(args), payload)
    print(f"节点心跳已上报: {node} ({payload['hostname']})")
    return 0


def cmd_runtimes(args: argparse.Namespace) -> int:
    from . import runtime_probe

    if args.explain:
        # Local diagnosis only: name where the search looked and why a
        # runtime was not found.  Nothing is pushed and no credential is
        # needed; these locations never enter a payload or normal output.
        print("\n".join(runtime_probe.explain()))
        return 0
    node = _resolve_node(args)
    url = _resolve_url(args)
    payload = runtime_probe.collect(node)
    runtime_probe.push(url, _read_token(args), payload)
    print(
        f"Runtime inventory reported: {node} "
        f"({len(payload['runtimes'])} CLI, {len(payload['data_dirs'])} data directories)"
    )
    return 0


def cmd_sync_sessions(args: argparse.Namespace) -> int:
    from .push_sessions import push_sessions

    url = _resolve_url(args)
    result = push_sessions(
        runtime=args.runtime,
        source=args.source,
        actor_id=args.actor,
        url=url,
        token=_read_actor_token(args),
        privacy=args.privacy,
        limit=args.limit,
        max_messages=args.max_messages,
    )
    print(
        f"会话同步完成({args.privacy}): 新增 {result['created']}, "
        f"更新 {result['updated']}, 未变化 {result['unchanged']}"
    )
    return 0


def cmd_enroll(args: argparse.Namespace) -> int:
    """Render (default) or install the schedule for the selected duties."""
    from . import enroll

    node = _resolve_node(args)
    url = _resolve_url(args)
    duty_keys = enroll.parse_duty_selection(args.duties)
    token_file = _resolve_token_file(args)
    try:
        token = Path(token_file).expanduser().read_text(encoding="utf-8").strip()
    except OSError:
        raise SystemExit(f"节点令牌文件不可读: {token_file}") from None
    if not token:
        raise SystemExit(f"节点令牌文件为空: {token_file}")
    actor_token_file = ""
    if enroll.DUTY_SESSIONS in duty_keys:
        actor_token_file = args.actor_token_file or os.environ.get(ENV_ACTOR_TOKEN_FILE)
        if not actor_token_file:
            raise SystemExit(
                f"会话职责需要执行者令牌文件: 传 --actor-token-file 或设置 {ENV_ACTOR_TOKEN_FILE}"
                "(节点令牌仅可上报心跳与 Runtime 清单, 会话同步归属执行者)"
            )
        try:
            actor_token = Path(actor_token_file).expanduser().read_text(encoding="utf-8").strip()
        except OSError:
            raise SystemExit(f"执行者令牌文件不可读: {actor_token_file}") from None
        if not actor_token:
            raise SystemExit(f"执行者令牌文件为空: {actor_token_file}")
        for name in ("runtime", "source", "actor"):
            if not getattr(args, name):
                raise SystemExit(f"会话职责需要 --{name}(选择了 sessions 职责)")
    config = enroll.config_from_values(
        node=node,
        url=url,
        token_file=token_file,
        actor_token_file=actor_token_file,
        runtime=args.runtime or "",
        source=args.source or "",
        actor=args.actor or "",
        privacy=args.privacy,
        duty_keys=duty_keys,
        package_path=args.package_path or os.environ.get(ENV_PACKAGE_PATH) or "",
    )
    if args.install:
        enroll.install(config, args.target)
        print(f"节点调度已安装: {node} ({args.target})")
    else:
        print(enroll.render(config, args.target))
    return 0


def cmd_whoami(args: argparse.Namespace) -> int:
    """Report what this node would send, without sending anything."""
    from . import probe, runtime_probe

    node = _resolve_node(args)
    token_file = (
        args.token_file or os.environ.get(ENV_TOKEN_FILE) or None
    )
    token_readable = False
    if token_file:
        try:
            token_readable = bool(
                Path(token_file).expanduser().read_text(encoding="utf-8").strip()
            )
        except OSError:
            token_readable = False
    report: dict = {
        "node_id": node,
        "server_url": _resolve_url(args),
        "token_file": {"path": token_file, "readable": token_readable},
        "heartbeat": probe.collect(node, args.label, _services(args)),
        "runtimes": runtime_probe.collect(node),
    }
    if args.source and args.actor:
        from adapters.exporters.sessions import collect_sessions

        report["sessions"] = collect_sessions(
            args.source,
            runtime=args.runtime,
            agent_id=args.actor,
            privacy=args.privacy,
            limit=args.limit,
            max_messages=args.max_messages,
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="retinue-node",
        description="节点代理: 心跳、Runtime 清单、会话同步(不依赖服务器组件)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser, *, token: bool = True) -> None:
        p.add_argument("--node", help=f"节点 id(或 {ENV_NODE})")
        p.add_argument("--url", help=f"服务器地址(或 {ENV_URL}, 默认 {DEFAULT_URL})")
        if token:
            p.add_argument("--token-file", help=f"节点令牌文件(或 {ENV_TOKEN_FILE})")

    heartbeat = sub.add_parser("heartbeat", help="上报本机节点健康心跳")
    add_common(heartbeat)
    heartbeat.add_argument("--label", default="")
    heartbeat.add_argument("--services", default="", help="逗号分隔的 systemd unit 允许清单")
    heartbeat.set_defaults(func=cmd_heartbeat)

    runtimes = sub.add_parser("runtimes", help="Report available Agent CLIs found on PATH")
    add_common(runtimes)
    runtimes.add_argument(
        "--explain",
        action="store_true",
        help="本地诊断: 列出每个 Runtime 的查找位置与结果, 解释未找到的原因; "
        "只打印不发送, 不需要令牌",
    )
    runtimes.set_defaults(func=cmd_runtimes)

    sessions = sub.add_parser(
        "sync-sessions", help="只读同步本机 Agent 会话索引、摘要或最近消息"
    )
    add_common(sessions)
    sessions.add_argument(
        "--actor-token-file",
        help=f"执行者令牌文件(或 {ENV_ACTOR_TOKEN_FILE}); "
        "会话同步归属执行者, 节点令牌对此路由无效",
    )
    sessions.add_argument("--runtime", choices=("claude-code", "codex"), required=True)
    sessions.add_argument("--source", required=True, help="运行时会话目录")
    sessions.add_argument("--actor", required=True)
    sessions.add_argument(
        "--privacy", choices=("metadata", "summary", "full"), default="metadata"
    )
    sessions.add_argument("--limit", type=int, default=50)
    sessions.add_argument("--max-messages", type=int, default=40)
    sessions.set_defaults(func=cmd_sync_sessions)

    whoami = sub.add_parser("whoami", help="打印本节点将上报的内容,但不发送")
    add_common(whoami)
    whoami.add_argument("--label", default="")
    whoami.add_argument("--services", default="", help="逗号分隔的 systemd unit 允许清单")
    whoami.add_argument("--runtime", choices=("claude-code", "codex"), default="claude-code")
    whoami.add_argument("--source", help="运行时会话目录(与 --actor 一起启用会话预览)")
    whoami.add_argument("--actor", help="会话预览的执行者 slug")
    whoami.add_argument(
        "--privacy", choices=("metadata", "summary", "full"), default="metadata"
    )
    whoami.add_argument("--limit", type=int, default=50)
    whoami.add_argument("--max-messages", type=int, default=40)
    whoami.set_defaults(func=cmd_whoami)

    enroll = sub.add_parser(
        "enroll",
        help="渲染(默认)或安装节点职责的调度; --target 必选, 安装需 --install",
    )
    add_common(enroll)
    enroll.add_argument(
        "--target",
        choices=("linux-system", "linux-user", "windows"),
        required=True,
        help="安装目标, 由 operator 显式选择, 不做猜测",
    )
    enroll.add_argument(
        "--install",
        action="store_true",
        help="实际写入并启用调度; 默认仅渲染, 不写不启用",
    )
    enroll.add_argument(
        "--duties",
        help="逗号分隔的职责子集: heartbeat,runtimes,sessions(默认全部三项); "
        "选择 sessions 时另需 --actor-token-file",
    )
    enroll.add_argument(
        "--actor-token-file",
        help=f"会话职责的执行者令牌文件(或 {ENV_ACTOR_TOKEN_FILE}); "
        "与心跳专用的节点令牌分开签发、分开保管",
    )
    enroll.add_argument(
        "--package-path",
        help=f"软件包在节点磁盘上的路径(或 {ENV_PACKAGE_PATH}); "
        "包未安装、以拷贝方式部署时必选, 渲染结果通过 PYTHONPATH 携带该路径",
    )
    enroll.add_argument("--runtime", choices=("claude-code", "codex"))
    enroll.add_argument("--source", help="运行时会话目录(选择 sessions 职责时必填)")
    enroll.add_argument("--actor", help="会话职责的执行者 slug(选择 sessions 职责时必填)")
    enroll.add_argument(
        "--privacy", choices=("metadata", "summary", "full"), default="metadata"
    )
    enroll.set_defaults(func=cmd_enroll)

    return parser


def main(argv: list[str] | None = None) -> int:
    configure_output_streams()
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
