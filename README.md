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

### 1. Start the hub with Compose

```bash
export RETINUE_ADMIN_PASSWORD=changeme1
docker compose up --build
```

Open <http://127.0.0.1:9219/> and sign in as `operator` with that password.
`GET /api/health` should return `{"status":"ok",...}` with no authentication.
The image is the authenticated v0.2 hub, not the old read-only panel.

### 2. Open a card

In **Task board → New task**, publish:

- Title: `Write hello.txt with: hello from retinue`
- Holder: leave as yourself for now, or onboard `worker-1` under
  Administration and assign that actor
- Priority: `high`
- Acceptance: `hello.txt contains exactly: hello from retinue`

Copy the generated task id.

### 3. Agent claim

Onboard an agent in Administration, save the one-time token outside the data
volume, then:

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

### 1. 用 Compose 起完整中枢

```bash
export RETINUE_ADMIN_PASSWORD=changeme1
docker compose up --build
```

打开 <http://127.0.0.1:9219/>，用 `operator` 和上面的密码登录。
`GET /api/health` 无需登录，应返回 `status=ok`。默认镜像是带登录的 v0.2
中枢，不再是旧只读面板。

### 2. 开一张卡

在任务看板新建任务：标题、持有人、优先级、验收条件。记下任务 id。

### 3. Agent 认领

在管理里给 agent 发一次性令牌，然后：

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

验收条件成立后把卡标为 `done`，在看板上读事件链。文件总线走廊见
[`docs/closed-loop-walkthrough.md`](docs/closed-loop-walkthrough.md)。安装、
备份和暴露警告见 [`SELF_HOSTING.md`](SELF_HOSTING.md)。

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
