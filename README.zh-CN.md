**[免安装查看 seed-42 静态演示 →](docs/demo/index.html)** · [English README](README.en.md) · [英文演示](https://jklthinking.github.io/retinue/demo-en/?lang=en) · [英文 PRD](docs/PRD.en.md) · [英文截图与版本介绍](docs/releases/2026-10-03-english-edition.md)

# Retinue

Retinue 是一个本地、文件驱动的 AI agent 协作控制面：给不同运行时派发任务，
在同一处查看任务状态、规范回执和 token 活动。它面向个人或小团队，强调持棒人
唯一、状态可观察，以及无需托管平台也能完整带走数据。

v0.1.0 MVP 聚焦单机闭环：YAML 任务卡、运行时 hook、只读 Web 面板、Claude
Code/Codex exporter、MCP 协作和可选的飞书适配器。上方静态演示只包含
`seed=42` 确定性样本，无需安装、服务进程或网络。

仓库还随附一个特定部署的站点控制台作为示例。它不属于 Retinue 核心，只有在
显式配置站点根目录后才会挂载，并且仍受管理员权限保护。

## 数据主权

Retinue 没有遥测、托管控制面、远程账号或强制外呼。核心、面板、daemon、demo
和 exporter 均可离线运行。文件模式的事实源是一个数据目录：`org.yaml`、
`tasks/`、`metrics/`、`nodes/`；停掉写入者后可复制、私有版本化，并在另一台机器恢复。
服务端事实源是 `retinue.db`，使用一致性 SQLite 快照备份。运行时源记录、外部成果、
凭据与部署配置分别恢复，详见[备份说明](SELF_HOSTING.md#backup)。
Exporter 只读运行时记录，不修改来源。只有显式启用的 IM adapter 会访问所配置
的服务。

完整边界见 [`docs/security.md`](docs/security.md)：agent 按不可信执行者处理，
hook 权威只来自 `org.yaml`，任务卡 chain 只增不改，agent 侧遵守
holder-only-writes，面板只读。

## 两分钟本地演示

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
retinue scan
```

安装后的第一条命令是 `retinue scan`：它列出本机已有的 agent CLI 和运行时
数据目录——纯离线，无需服务端、账号或令牌——让你第一眼看到的是自己机器上的
真实 fleet，而不是样本数据。加 `--json` 可输出同样内容的机器可读版本。

下面的 demo 是在虚构的 seed-42 fleet 上的导览：

```bash
retinue demo
```

打开 <http://127.0.0.1:8787/overview>。样本固定为 1 个节点、3 个 agent、6 张
任务卡和 7 天 token 曲线；非空目录不会被覆盖。重建仓库内离线演示：

```bash
python scripts/build_static_demo.py --output docs/demo
```

真实冷启动可先运行 `retinue demo retinue-demo --no-serve`，再按需执行
`retinue export claude-code` / `retinue export codex`，用 `retinue task new`
建卡，以 `retinue daemon retinue-demo --node laptop --once` 触发本机运行时，最后
用 `retinue receipt`、`retinue task lint` 和 `retinue panel` 验证闭环。MCP agent
接入见 [`docs/agent-onboarding.md`](docs/agent-onboarding.md)。MCP 表面是可选
extra（`pip install '.[mcp]'`），基础安装与节点安装不携带 ASGI 服务器或密码学
库。

## Adapter 路线图

公开顺序为：**飞书 / Lark ✅ → 企业微信 / WeCom → 钉钉 / DingTalk →
Telegram / Slack**。飞书 adapter 已进入 v0.1.0；Telegram 受平台 bot-to-bot
限制时继续使用文件总线兜底。Claude Code 和 Codex exporter 已内置。新增运行时
或 IM 通道应保持薄适配，贡献契约见
[`docs/adapters-guide.md`](docs/adapters-guide.md)。

## 支持矩阵

“已验证”只表示 v0.1.0 实际跑过；未验证项不是承诺。

| 能力 | Linux | macOS | Windows |
|---|---|---|---|
| 任务协议、CLI、demo、面板 | **Python 3.12 已验证** | Python 3.10+ 预期可用，未实测 | WSL2 预期可用；原生未实测 |
| daemon argv hook | **已验证** | 未实测 | WSL2 未实测；原生命令 quoting 未验证 |
| Claude Code / Codex exporter | **用本地 JSONL 已验证** | 格式兼容，未实测 | 记录位置和原生行为未验证 |
| Docker Compose 面板 | **Linux 已验证** | Docker Desktop 预期可用，未实测 | Docker Desktop/WSL2 预期可用，未实测 |
| 飞书 adapter | 协议测试通过，既有真实部署验证 | 未按 OS 验证 | 未按 OS 验证 |

支持 Python 3.10 及以上版本。负责执行任务的节点必须另行安装对应运行时 CLI。

## 多设备智能体发现

众卿将**节点健康**和**运行时可用性**分开处理。每台节点可每日主动上报一次：本机
`PATH` 及常见用户安装目录里有哪些受支持的 Agent CLI 命令可用：

```bash
python -m server.main probe-runtimes --node laptop --url https://retinue.example.invalid --token-file /secure/path/node.token
```

复用节点健康检查的节点令牌即可。该令牌只可用于本节点的心跳与运行时清单接口，不能读取
会话或创建任务。探针只判断已知命令名能否解析；不会读取终端配置、会话文件、环境变量、
凭据或可执行文件的绝对路径。建议由操作系统计划任务每日执行一次。

上报前须由操作员明确准入节点。首次为未知节点签发节点令牌时，会在同一事务中完成准入并
记录决定，因此新机器不会因隐藏的第二步而被拒绝。也可通过 `POST /api/admin/nodes` 单独
准入，通过 `DELETE /api/admin/nodes/{node_id}` 退役。只有与节点精确绑定的节点令牌能
上报心跳或运行时清单；agent 令牌和管理员会话都不能冒充节点遥测。退役会禁用节点令牌并
将其移出活动 fleet，同时保留最后一次心跳和运行时清单作为历史。详见
[`docs/protocol/node-membership.md`](docs/protocol/node-membership.md)。

## 诚实的规模边界

文件后端的设计护栏约为 **50 个 agent、每 workspace 10,000 张任务卡**。这不是
benchmark、SLA 或硬限制：目录扫描是线性的，写入并发按小规模设计，也没有分布式
事务。应归档已完成卡、每次变更保持一个事实写入者，并在同步场景运行冲突副本
lint。

演进路径是：单节点索引需求先迁 SQLite，多节点并发再迁 PostgreSQL。v0.1.0
不宣称已经具备这些能力。三页抽取、更多 exporter、富 IM 卡片/Issue 工作流和
多节点属于 v0.1.x/v0.2.0。

## 自托管与开发

安装、升级、备份/恢复演练、Compose 以及端口暴露警告见
[`SELF_HOSTING.md`](SELF_HOSTING.md)。协议文档位于
[`docs/protocol/`](docs/protocol/)，安全边界位于
[`docs/security.md`](docs/security.md)。项目采用 MIT 许可证。
