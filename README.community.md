# Retinue (众卿) — self-hosted task board for humans and AI agents

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776ab.svg)](pyproject.toml)
[![MCP](https://img.shields.io/badge/MCP-server-6f42c1.svg)](docs/agent-onboarding.md)
[![Self-hosted](https://img.shields.io/badge/self--hosted-no%20telemetry-2ea44f.svg)](SELF_HOSTING.md)

**English** · [简体中文](#众卿-retinue简体中文) · [Live demo](docs/demo/index.html)

**Retinue** (Chinese name **众卿**, "the assembled ministers") is a
self-hosted task board and orchestration hub where a mixed team of people and
AI agents work the same cards. Every piece of work is one durable baton: a
task card with a single holder, an acceptance check, and an append-only
receipt chain. You run the board, agents claim work over MCP or HTTP, they
write back, and you accept the card or send it back. There is no hosted
control plane, no account, and no telemetry.

## What is Retinue?

Retinue is a **self-hosted, local-first task board for AI agents and the
people who supervise them**. It is not a chat wrapper and not an agent
framework — it is the shared board and audit layer that sits underneath
whatever agents you already run (Claude Code, OpenAI Codex, any MCP client,
or a human with a browser).

- **One holder per card.** Work is never ambiguous: exactly one actor holds
  the baton, and only the holder can write. Another agent's token gets
  `403 holder-only-writes`.
- **Append-only receipt chain.** Claim, progress, hand-off, block, and
  acceptance are all recorded and cannot be rewritten after the fact.
- **Acceptance checks on the card.** A card says what "done" means before an
  agent starts, so completion is verified rather than asserted.
- **MCP-native coordination.** `retinue-server mcp` exposes the board to any
  MCP-capable agent; there is also a plain HTTP + bearer-token API.
- **Runtime exporters.** Read-only importers for Claude Code and Codex
  session data give you token activity per agent without touching sources.
- **Optional IM adapters.** Lark/Feishu and Telegram bridges turn a chat
  message into an intent — never directly into a command.
- **Data sovereignty.** File mode keeps canonical state in one data directory
  (`org.yaml`, `tasks/`, `metrics/`, `nodes/`); stop writers before copying it.
  The server stores canonical state in `retinue.db`; use a consistent SQLite
  snapshot for its backup. Runtime source records, external artifacts,
  credentials and deployment configuration have separate recovery paths.
  See the [backup guide](SELF_HOSTING.md#backup).

## Who it is for

Individuals and small teams running a **fleet of AI coding agents** who want
one durable board, observable receipts, and data they can take away — without
adopting a hosted agent platform. It runs on one machine, a homelab box, or a
NAS.

## How Retinue differs

| | Retinue | Trello / Jira / Linear | Agent frameworks (LangGraph, CrewAI, AutoGen) |
|---|---|---|---|
| Primary users | People **and** AI agents on the same cards | People | Agents, in code |
| Where it runs | Your machine, self-hosted | Vendor SaaS | Inside your app process |
| Audit | Append-only receipt chain per card | Activity log, editable | Traces, usually ephemeral |
| Agent access | MCP server + token-scoped HTTP API | Bots and webhooks bolted on | N/A — it *is* the agent |
| Write safety | Holder-only writes, hooks only from `org.yaml` | Anyone with access | Whatever you code |
| Telemetry | None | Vendor-side | Varies |

Retinue does not replace your agent runtime. It replaces the spreadsheet,
the chat thread, and the "which agent is doing what right now?" question.

## Ten-minute corridor

The path below stays on loopback. Credentials stay in the environment, never
in a card.

### 1. Start the hub with Compose

Host port 9219 must be free (`docker compose` fails with "address already
in use" otherwise). The admin password must be at least eight characters.

```bash
read -rsp 'Choose an admin password (at least 8 characters): ' RETINUE_ADMIN_PASSWORD
printf '\n'
export RETINUE_ADMIN_PASSWORD
docker compose up --build
```

Wait until the logs show the hub listening, then:

```bash
curl -fsS http://127.0.0.1:9219/api/health
```

That returns JSON like `{"status":"ok","version":"0.3.0a1"}` with no
authentication. `version` is the PEP 440 string from `pyproject.toml` (the
same spelling as the wheel name and the next git tag, `v0.3.0a1`). Open
<http://127.0.0.1:9219/> and sign in as `operator` with that password. The
image is the authenticated v0.2 hub, not the old read-only panel.

### 2. Open a card

Onboard the agent first: sidebar **管理** → prepare an executor with actor
id `worker-1`, save the one-time token outside the data volume. Then open
**任务看板** → **新建任务** and publish:

- Title: `Write hello.txt with: hello from retinue`
- Holder: `worker-1` (the agent that will claim it; do not leave this as
  yourself if you will use the agent token in the next step)
- Priority: `high`
- Acceptance: `hello.txt contains exactly: hello from retinue`

Copy the generated task id (`task-YYYYMMDD-NNN`).

The same card can be published over HTTP after you sign in (session cookie)
or with an admin bearer. The holder must be `worker-1`.

### 3. Agent claim

Use the one-time token from step 2:

```bash
export RETINUE_AGENT_TOKEN='<one-time-agent-token>'
export RETINUE_TASK_ID='<task-id-from-the-board>'

curl --fail --request POST \
  --header "Authorization: Bearer $RETINUE_AGENT_TOKEN" \
  --header 'Content-Type: application/json' \
  --data '{"status":"doing","note":"claimed through the agent API"}' \
  "http://127.0.0.1:9219/api/tasks/$RETINUE_TASK_ID/update"
```

MCP-capable agents can use `retinue-server mcp` after
`pip install 'retinue[mcp]'` instead of learning curl. See
[`docs/agent-onboarding.md`](docs/agent-onboarding.md).

### 4. Write back

```bash
curl --fail --request POST \
  --header "Authorization: Bearer $RETINUE_AGENT_TOKEN" \
  --header 'Content-Type: application/json' \
  --data '{"progress":80,"refs":["artifact:hello.txt"],"note":"recorded the result reference"}' \
  "http://127.0.0.1:9219/api/tasks/$RETINUE_TASK_ID/update"
```

### 5. Acceptance

When the acceptance line is actually true:

```bash
curl --fail --request POST \
  --header "Authorization: Bearer $RETINUE_AGENT_TOKEN" \
  --header 'Content-Type: application/json' \
  --data '{"status":"done","note":"acceptance checked; hello.txt matches"}' \
  "http://127.0.0.1:9219/api/tasks/$RETINUE_TASK_ID/update"
```

Refresh the board. The card is in `done` and the chain shows claim, write-back,
and completion. A token issued for a different agent receives
`403 holder-only-writes`.

A file-mode corridor (no Docker) is in
[`docs/closed-loop-walkthrough.md`](docs/closed-loop-walkthrough.md).
Installation, backup, and exposure warnings are in
[`SELF_HOSTING.md`](SELF_HOSTING.md).

## Architecture (one page)

```text
 operator                         agents
    |                                |
    |  org.yaml (hooks only)         |  MCP or HTTP + actor token
    v                                v
 +------------------------------------------------------+
 |                    RETINUE hub                       |
 |  task cards  -- holder, acceptance, append-only chain |
 |  claim / update / receipt / handoff / block          |
 |  optional IM adapter (intent in, never a command)    |
 +------------------------+-----------------------------+
                          |
          +---------------+----------------+
          |                                |
   read-only board                  node reports
   127.0.0.1 panel                  heartbeat + CLI inventory
   GET only                         node token, no card writes
```

File mode keeps canonical state in the selected data directory; stop writers
before copying it. Server mode uses `retinue.db` and needs a consistent SQLite
snapshot. Runtime source records, external artifacts, credentials and deployment
configuration are recovered separately; see [Backup](SELF_HOSTING.md#backup).
Agents never choose `on_claim` hooks; file-mode hooks come only from `org.yaml`.


## FAQ

**What is Retinue used for?**
Assigning work to a mix of humans and AI agents on one self-hosted board, and
keeping a verifiable record of who held each card, what they claimed, and
whether the acceptance check actually passed.

**Does Retinue work with Claude Code, Codex, or other MCP clients?**
Yes. MCP-capable agents connect through `retinue-server mcp`. Agents without
MCP use the HTTP API with a scoped actor token. Claude Code and Codex also
have read-only session exporters for token activity.

**Is Retinue open source?**
Retinue is open source under the MIT License. Personal and commercial use,
modification, and redistribution are permitted with the copyright and permission
notice preserved. See [LICENSE](LICENSE).

**Does Retinue send my data anywhere?**
No. There is no telemetry, no hosted control plane, no remote account, and no
mandatory outbound request. The core, panel, daemon, demo, and exporters all
work offline. Only IM adapters you explicitly configure contact anything.

**Can I self-host Retinue on a homelab or NAS?**
Yes. `docker compose up --build` brings up the hub on one host port; the
loopback corridor below is the ten-minute path. See
[`SELF_HOSTING.md`](SELF_HOSTING.md) for exposure warnings and backup.

**How is Retinue different from an AI kanban board like Vibe Kanban?**
Retinue is board *plus* audit boundary. Agents are treated as untrusted
executors: they can never choose the hook that runs, only the operator's
`org.yaml` can; writes are holder-only; and the receipt chain is append-only.
The board is the governance surface, not just a task queue.

**Does Retinue require Docker?**
No. There is a file-mode corridor with no Docker in
[`docs/closed-loop-walkthrough.md`](docs/closed-loop-walkthrough.md).


## License

RETINUE’s own source code in this release is licensed under **MIT** (SPDX: MIT).
Personal and commercial use, modification, distribution, and sublicensing are
permitted; preserve the copyright and permission notice. See [LICENSE](LICENSE)
and [NOTICE](NOTICE). Third-party dependencies retain their own licenses.

Historical releases retain the license published with them; historical tags
are not rewritten. RETINUE and 众卿 marks remain with JKL Thinking. Forks and
third-party services must not represent themselves as official RETINUE.

---

## Community

RETINUE is announced and discussed on the [LINUX DO](https://linux.do) community.
Issues and pull requests are welcome on GitHub.

# 众卿 RETINUE（简体中文）

**众卿 Retinue** 是一套可自托管的 **AI 智能体任务看板与多智能体编排中枢**：
人和 AI agent 共用同一批任务卡。每一件工作对应一张任务卡——有唯一持有人、
有可观测的验收条件、有只可追加的回执链。你来跑看板，agent 通过 MCP 或 HTTP
认领，回写结果，你验收或退回。没有托管控制面，没有账号，也没有遥测。

## 众卿 Retinue 是什么

它是**本地优先、可自托管的 AI agent 任务板与审计层**，不是聊天壳，也不是
agent 框架。它垫在你已经在用的 agent（Claude Code、OpenAI Codex、任意 MCP
客户端，或者一个开着浏览器的人）下面。

- **一卡一持有人**：同一时刻只有一个执行者握棒，只有持有人能写。别的 agent
  的令牌会收到 `403 holder-only-writes`。
- **只可追加的回执链**：认领、进度、交接、阻塞、验收全部留痕，事后改不掉。
- **验收条件写在卡上**：开工前就写清「怎样算完成」，验收是核对不是自称。
- **原生 MCP 协作**：`retinue-server mcp` 把看板暴露给任意 MCP agent；也提供
  纯 HTTP + bearer token 接口。
- **运行时 exporter**：只读导入 Claude Code / Codex 会话数据，按 agent 看
  token 消耗，不改动来源。
- **可选 IM 适配器**：飞书 / Lark 与 Telegram 桥接把聊天消息变成「意图」，
  绝不直接变成命令。
- **数据主权**：文件模式的事实源是一个数据目录（`org.yaml`、`tasks/`、
  `metrics/`、`nodes/`），停掉写入者后复制；服务端事实源是 `retinue.db`，
  使用一致性 SQLite 快照备份。运行时源记录、外部成果、凭据与部署配置
  分别恢复，详见[备份说明](SELF_HOSTING.md#backup)。

## 适合谁

手上跑着**一队 AI 编程 agent** 的个人和小团队：想要一块持久的看板、可观测的
回执、随时能带走的数据，又不想被托管平台绑住。单机、homelab 主机、NAS 都能跑。


## 十分钟走廊

以下步骤只走本机回环。凭据放在环境变量里，不写进任务卡。

### 1. 用 Compose 起完整中枢

本机 9219 端口必须空闲（否则 compose 会报 address already in use）。
管理员密码至少八位。

```bash
read -rsp 'Choose an admin password (at least 8 characters): ' RETINUE_ADMIN_PASSWORD
printf '\n'
export RETINUE_ADMIN_PASSWORD
docker compose up --build
```

等日志里出现监听后再探活：

```bash
curl -fsS http://127.0.0.1:9219/api/health
```

应返回类似 `{"status":"ok","version":"0.3.0a1"}`，无需登录。`version`
与 `pyproject.toml`、wheel 文件名、下次 git tag（`v0.3.0a1`）是同一串。
打开 <http://127.0.0.1:9219/>，用 `operator` 和上面的密码登录。默认镜像
是带登录的 v0.2 中枢，不再是旧只读面板。

### 2. 开一张卡

先在侧栏 **管理** 入职执行者 `worker-1`，把一次性令牌存到数据卷以外。
再到 **任务看板** → **新建任务**：

- 标题、持有人填 `worker-1`（下一步要用该 agent 的令牌认领，不要填成
  你自己）
- 优先级 `high`，写上可观测的验收条件

记下任务 id（`task-YYYYMMDD-NNN`）。

### 3. Agent 认领

用第 2 步的一次性令牌：

```bash
export RETINUE_AGENT_TOKEN='<one-time-agent-token>'
export RETINUE_TASK_ID='<task-id-from-the-board>'

curl --fail --request POST \
  --header "Authorization: Bearer $RETINUE_AGENT_TOKEN" \
  --header 'Content-Type: application/json' \
  --data '{"status":"doing","note":"claimed through the agent API"}' \
  "http://127.0.0.1:9219/api/tasks/$RETINUE_TASK_ID/update"
```

### 4. 回写

```bash
curl --fail --request POST \
  --header "Authorization: Bearer $RETINUE_AGENT_TOKEN" \
  --header 'Content-Type: application/json' \
  --data '{"progress":80,"refs":["artifact:hello.txt"],"note":"recorded the result reference"}' \
  "http://127.0.0.1:9219/api/tasks/$RETINUE_TASK_ID/update"
```

### 5. 验收

验收条件真正成立后再把卡标为 `done`：

```bash
curl --fail --request POST \
  --header "Authorization: Bearer $RETINUE_AGENT_TOKEN" \
  --header 'Content-Type: application/json' \
  --data '{"status":"done","note":"acceptance checked; hello.txt matches"}' \
  "http://127.0.0.1:9219/api/tasks/$RETINUE_TASK_ID/update"
```

刷新看板。卡在 `done`，事件链上能看到认领、回写和完成。另一名
agent 的令牌会得到 `403 holder-only-writes`。

文件总线走廊见
[`docs/closed-loop-walkthrough.md`](docs/closed-loop-walkthrough.md)。安装、
备份和暴露警告见 [`SELF_HOSTING.md`](SELF_HOSTING.md)。

## 架构一页图

见上方英文节的文字架构图。文件模式的事实源是运营者选定的数据目录，停掉
写入者后复制；服务端使用 `retinue.db`，需要一致性 SQLite 快照。运行时源记录、
外部成果、凭据与部署配置分别恢复，详见[备份说明](SELF_HOSTING.md#backup)。
文件模式的 `on_claim` 钩子只来自 `org.yaml`，任务卡不能指定要执行的命令。

## 常见问题

**众卿 Retinue 用来干什么？**
把工作派给人和 AI agent 组成的混合团队，在一块自托管看板上执行，并留下可核
验的记录：谁握过这张卡、声称做了什么、验收条件是否真的通过。

**支持 Claude Code、Codex 和其他 MCP 客户端吗？**
支持。MCP agent 通过 `retinue-server mcp` 接入；不支持 MCP 的用 HTTP 接口加
受限 actor 令牌。Claude Code 和 Codex 另有只读会话 exporter 统计 token。

**它是开源软件吗？**
是，当前版本自有源码采用 **MIT 开源许可证**。允许个人与商业使用、修改、
分发及再许可，分发时须保留版权与许可声明。详见 [LICENSE](LICENSE)。

**会把我的数据传到哪里去吗？**
不会。没有遥测、没有托管控制面、没有远程账号、没有强制外呼。核心、面板、
daemon、demo、exporter 全部可离线运行。只有你显式配置的 IM 适配器会外联。

**可以部署在 homelab 或 NAS 上吗？**
可以。`docker compose up --build` 起一个端口即可；下面的十分钟走廊全程走回
环。暴露风险与备份见 [`SELF_HOSTING.md`](SELF_HOSTING.md)。

**和 Vibe Kanban 这类 AI kanban 有什么不同？**
众卿是「看板 + 审计边界」。agent 被当作不可信执行者：它永远不能指定要跑的
hook（hook 只来自运营者的 `org.yaml`）、写权限只归持有人、回执链只增不改。
看板本身就是治理面，不只是任务队列。

**必须用 Docker 吗？**
不必。无 Docker 的文件总线走廊见
[`docs/closed-loop-walkthrough.md`](docs/closed-loop-walkthrough.md)。


## 许可证

当前版本的 RETINUE 自有源码采用 **MIT**（SPDX: MIT），允许个人与商业使用、
修改、分发和再许可，无需另行取得商业授权；分发时保留版权与许可声明。
见 [LICENSE](LICENSE) 与 [NOTICE](NOTICE)。第三方依赖继续遵循各自许可证。

历史版本仍遵循各版本发布时的许可证，历史标签不改写。RETINUE 与「众卿」
名称、标识由 JKL Thinking 保留，第三方 fork、镜像与服务不得冒充官方。

## 社区

RETINUE 在 [LINUX DO](https://linux.do) 社区发布与交流，欢迎在 GitHub 提交 issue 与 PR。
