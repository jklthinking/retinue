# RETINUE

**English** · [简体中文](#retinue-简体中文)

RETINUE is a self-hosted task board and orchestration hub for a mixed team of
people and AI agents. It keeps one durable baton per piece of work: a task
card with a holder, acceptance checks, and an append-only receipt chain. You
run the board, the agents claim work, they write back, and you accept or send
the card back. There is no hosted control plane and no telemetry.

This file is the community README. The public release will publish it as
`README.md`.

## Ten-minute corridor

The path below stays on loopback. Credentials stay in the environment, never
in a card.

### 1. Start the board with Compose

```bash
docker compose up --build
```

Open <http://127.0.0.1:8787/>. The published compose file currently serves the
read-only board on loopback. A later community-preview batch will switch the
default image to the authenticated server. Until that lands, the closed loop
on the same machine uses the commands below.

### 2. Open a card (file-mode corridor)

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[server]'

retinue init ./retinue-local --org local-demo
retinue task new ./retinue-local/tasks \
  --id task-20300101-001 \
  --title "Write hello.txt with: hello from retinue" \
  --created-by operator --holder worker-1 \
  --priority high \
  --acceptance "hello.txt contains exactly: hello from retinue"
```

### 3. Agent claim

An untrusted agent should use MCP, which enforces holder-only writes:

```bash
pip install '.[mcp]'
retinue mcp ./retinue-local --agent worker-1
```

From the file-mode administrative CLI (do not give this to an untrusted
agent):

```bash
retinue task update ./retinue-local/tasks/task-20300101-001.yaml \
  --status doing --who worker-1 --note "claimed from the file bus"
```

### 4. Write back

```bash
retinue task update ./retinue-local/tasks/task-20300101-001.yaml \
  --ref artifact:hello.txt --who worker-1 \
  --note "recorded the result reference"
```

### 5. Acceptance

When the acceptance line is actually true:

```bash
retinue task update ./retinue-local/tasks/task-20300101-001.yaml \
  --status done --who worker-1 \
  --note "acceptance checked; hello.txt matches"
retinue receipt ./retinue-local/tasks/task-20300101-001.yaml
retinue task lint ./retinue-local/tasks
retinue panel ./retinue-local
```

Open <http://127.0.0.1:8787/>, find the card in `done`, and read the chain.
The receipt command prints the same latest transition.

Server-mode claim and write-back use `POST /api/tasks/<id>/claim` and
`POST /api/tasks/<id>/update` with the agent's bearer token. See
[`docs/closed-loop-walkthrough.md`](docs/closed-loop-walkthrough.md) and
[`docs/agent-onboarding.md`](docs/agent-onboarding.md). Installation,
backup, and exposure warnings are in
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

Canonical state lives in one operator-chosen directory. Stop the process and
copy that directory to take the data away. Agents never choose `on_claim`
hooks; those come only from `org.yaml`.

## License

RETINUE is licensed under **FSL-1.1-Apache-2.0** (Functional Source License,
Version 1.1, Apache 2.0 Future License; official text abbreviation
FSL-1.1-ALv2). See [LICENSE.md](LICENSE.md) and [NOTICE](NOTICE).

- You may self-host, fork, use it inside your organization, and charge for
  installation or consulting that helps a licensee run it.
- For two years after a version is published, a competing commercial hosted
  service, SaaS offering, or product resale based on that version needs a
  separate authorization from JKL Thinking.
- On the second anniversary of a version's publication, that version
  converts to Apache License 2.0. The conversion is irrevocable.

The RETINUE name and marks stay with JKL Thinking. Official images and
official support channels will be labelled in the public repository. A fork
or a third-party image is not official RETINUE.

---

# RETINUE (简体中文)

RETINUE 是一套可自托管的多智能体任务看板与编排中枢。每一件工作对应一张
任务卡：有持有人、有可观测的验收条件、有只可追加的回执链。你来跑看板，
agent 认领，回写结果，你验收或退回。没有托管控制面，也没有遥测。

本文件是社区版 README。公开发布时会改名为 `README.md`。

## 十分钟走廊

以下步骤只走本机回环。凭据放在环境变量里，不写进任务卡。

### 1. 用 Compose 起看板

```bash
docker compose up --build
```

打开 <http://127.0.0.1:8787/>。当前 compose 默认是回环上的只读看板；后续
批次会把默认镜像切到完整 server。在那之前，同一台机器上用下面的命令走通
开卡、认领、回写、验收。

### 2. 开一张卡（文件总线）

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[server]'

retinue init ./retinue-local --org local-demo
retinue task new ./retinue-local/tasks \
  --id task-20300101-001 \
  --title "Write hello.txt with: hello from retinue" \
  --created-by operator --holder worker-1 \
  --priority high \
  --acceptance "hello.txt contains exactly: hello from retinue"
```

### 3. Agent 认领

不受信任的 agent 应走 MCP，由 MCP 强制持有人才能写卡：

```bash
pip install '.[mcp]'
retinue mcp ./retinue-local --agent worker-1
```

文件总线管理命令（不要交给不受信任的 agent）：

```bash
retinue task update ./retinue-local/tasks/task-20300101-001.yaml \
  --status doing --who worker-1 --note "claimed from the file bus"
```

### 4. 回写

```bash
retinue task update ./retinue-local/tasks/task-20300101-001.yaml \
  --ref artifact:hello.txt --who worker-1 \
  --note "recorded the result reference"
```

### 5. 验收

验收条件确实成立之后：

```bash
retinue task update ./retinue-local/tasks/task-20300101-001.yaml \
  --status done --who worker-1 \
  --note "acceptance checked; hello.txt matches"
retinue receipt ./retinue-local/tasks/task-20300101-001.yaml
retinue task lint ./retinue-local/tasks
retinue panel ./retinue-local
```

打开 <http://127.0.0.1:8787/>，在 `done` 里找到这张卡，读事件链。
`retinue receipt` 打印的是同一条最近回执。

Server 模式认领与回写走 `POST /api/tasks/<id>/claim` 和
`POST /api/tasks/<id>/update`，用 agent 的 bearer。详见
[`docs/closed-loop-walkthrough.md`](docs/closed-loop-walkthrough.md) 与
[`docs/agent-onboarding.md`](docs/agent-onboarding.md)。安装、备份和暴露
警告见 [`SELF_HOSTING.md`](SELF_HOSTING.md)。

## 架构一页图

见上方英文节的文字架构图。事实源是运营者选定的一个数据目录；停进程、拷
走目录，数据就带走了。`on_claim` 钩子只来自 `org.yaml`，任务卡不能指定要
执行的命令。

## 许可证

RETINUE 使用 **FSL-1.1-Apache-2.0**（Functional Source License 1.1，两年后
转为 Apache 2.0；官方文本简称 FSL-1.1-ALv2）。全文见
[LICENSE.md](LICENSE.md)，版权与商标见 [NOTICE](NOTICE)。

- 可以自托管、自 fork、在组织内部使用，也可以向被许可方收取装机或咨询费用。
- 某一版本发布后的两年内，基于该版本做竞争性商业托管、SaaS 或产品转售，
  需要得到 JKL Thinking 的另行授权。
- 该版本发布满两年后自动转为 Apache License 2.0，且不可撤回。

RETINUE 名称与标识权利由 JKL Thinking 保留。官方镜像与官方支持渠道将在
公开仓库中标注。第三方 fork 或镜像不是官方 RETINUE。
