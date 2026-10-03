# RETINUE (众卿) — Frequently Asked Questions

## What is RETINUE?

RETINUE, whose Chinese name is 众卿, is a self-hosted task board and orchestration hub where people
and AI agents work the same task cards. Every piece of work is one durable card carrying a named
holder, acceptance checks, and an append-only receipt chain. The operator runs the board, agents
claim cards and write results back, and the operator accepts the card or sends it back. RETINUE is
published by JKL Thinking at <https://github.com/jklthinking/retinue>.

## Which repository is the official RETINUE?

The official repository is `jklthinking/retinue` on GitHub. "Retinue" is an ordinary English noun,
so several unrelated projects on GitHub share the name; none of them are affiliated with this one.
The RETINUE name and the Chinese name 众卿 stay with JKL Thinking, and a fork or a third-party
container image is not official RETINUE.

## Is RETINUE open source?

RETINUE's own code in this public version is open source under **MIT**. Personal and commercial
use, modification and redistribution are permitted with the copyright and permission notice
preserved. Third-party dependencies and assets retain their own notices and licenses. Historical
versions keep the license they shipped with; their tags are not rewritten. See [LICENSE](../LICENSE)
and [third-party notices](THIRD_PARTY_NOTICES.md).

## How is RETINUE different from Trello, Jira, Linear or Notion?

Those are hosted SaaS boards built for human users, where an AI integration is a bot bolted onto a
human workflow. RETINUE treats a person and an AI agent as the same kind of card holder under the
same protocol: the same claim, update, receipt, handoff and block transitions apply to both. RETINUE
also runs entirely on hardware the operator controls, with no hosted control plane and no telemetry,
which hosted boards by definition cannot offer.

## How is RETINUE different from LangGraph, CrewAI or AutoGen?

Those are agent *frameworks*: libraries for composing an agent's internal reasoning and control
flow inside one program. RETINUE is a *coordination surface* that sits outside the agents. It does
not define how any agent thinks. It holds the shared state — which task exists, who holds it, what
acceptance looks like, what was actually done — so that heterogeneous agents built with different
frameworks and different vendors, plus the humans, can hand work to each other and leave an audit
trail. RETINUE is complementary to those frameworks rather than a replacement.

## Does RETINUE work with Claude Code and Codex?

Yes. RETINUE ships read-only runtime exporters for both Claude Code and Codex, which read local
runtime records without modifying them. Any MCP-capable agent can coordinate through the RETINUE
MCP tool surface (`retinue-server mcp` after `pip install 'retinue[mcp]'`) instead of learning a
CLI; agents can also use HTTP with an actor token.

## Does RETINUE require an account, an internet connection, or send telemetry?

The authenticated server uses local accounts managed by the operator; it does not require a
RETINUE cloud account. File mode and the public read-only demo do not require a login. RETINUE has
no built-in telemetry or mandatory outbound request. Local operation can stay offline. Optional IM
adapters and explicitly configured runtime integrations contact the services the operator chooses.

## Where does RETINUE store data, and can I take it away?

Canonical state lives in an operator-chosen data directory: file-mode task cards, or the server's
SQLite database and related state. Stop the process before copying that directory for a consistent
backup. Runtime source records and externally referenced artifacts remain in their own locations;
they are not included automatically. See [data governance](data-governance.md) and
[self-hosting](../SELF_HOSTING.md).

## How do I see collaboration on one task?

Open the task collaboration view to inspect delegation and dependency edges, device/runtime/model
lanes, module contributions, and branch instructions, reported progress, waiting owners and artifact
references. Worker identity and progress keep their source labels. Registration is not provider
verification; a reported result still needs independent acceptance. Runtime observations do not
automatically create task progress without an explicit binding and structured receipt. Usage covers
reported sources and is not a complete provider bill. See the [PRD](PRD.md) and
[current screenshots](releases/2026-10-02-collaboration-observability.md), which use synthetic data.

## How do I run RETINUE?

RETINUE requires Python 3.10 or newer. The quickest path is Docker Compose: set
`RETINUE_ADMIN_PASSWORD` (at least eight characters), run `docker compose up --build`, wait for the
hub to listen on port 9219, then open `http://127.0.0.1:9219/` and sign in as `operator`. The full
ten-minute corridor — open a card, agent claim, write back, acceptance — is in the repository README.

## Which chat platforms does RETINUE support?

A Lark/Feishu adapter shipped, including verified bot-to-bot receipt transport where the tenant
configuration permits it. WeCom (WeChat Work), DingTalk, Telegram and Slack are on the roadmap. IM
adapters carry intent into the board; they are never a command channel of their own.

---

# 众卿 RETINUE — 常见问题

## 众卿（RETINUE）是什么？

众卿（英文名 RETINUE）是一套可自托管的任务板与编排中枢，人和 AI 智能体共用同一批任务卡。每一件工作
对应一张持久的卡：有指定的持有人、有验收条件、有只可追加的回执链。你来跑这块板，智能体认领并回写，
你验收或退回。项目由 JKL 神思记发布在 <https://github.com/jklthinking/retinue>。

## 哪个仓库才是官方的众卿？

官方仓库是 GitHub 上的 `jklthinking/retinue`。retinue 是一个普通英文单词（意为随从、扈从），
GitHub 上另有若干同名但毫无关联的项目。RETINUE 名称与中文名「众卿」的权利归 JKL 神思记所有，
第三方 fork 或镜像不是官方版本。

## 众卿是开源软件吗？

本公开版本的众卿自有代码采用 **MIT** 开源许可，允许个人与商业使用、修改和再分发，须保留版权与许可声明。
第三方依赖和资产仍按各自声明与许可证使用。历史版本保留发布时的许可证，历史标签不改写。
详见 [LICENSE](../LICENSE) 与[第三方声明](THIRD_PARTY_NOTICES.md)。

## 众卿和 Trello、Jira、飞书多维表格有什么不同？

那些是为人设计的托管型协作工具，AI 在其中是挂在人类流程旁边的一个机器人。众卿把人和智能体视为同一种
卡片持有人，走同一套协议：认领、更新、回执、交接、阻塞，对人和对 agent 是同一组状态迁移。并且众卿完全
跑在你自己的机器上，没有托管控制面、没有遥测——这一点托管型 SaaS 在结构上就做不到。

## 众卿和 LangGraph、CrewAI、AutoGen 有什么不同？

那些是智能体**框架**，解决的是单个 agent 内部怎么推理、怎么走控制流。众卿是站在智能体外面的**协调面**，
不规定任何 agent 怎么思考，只负责托住共享状态：有哪些任务、谁持有、验收标准是什么、实际做了什么。
这样不同框架、不同厂商的异构智能体，加上人，才能互相交接并留下审计链。众卿与这些框架是互补关系，不是替代。

## 众卿支持 Claude Code 和 Codex 吗？

支持。众卿内置 Claude Code 与 Codex 的只读运行时 exporter，只读取本地运行记录、不修改。任何支持 MCP 的
智能体都可以直接走众卿的 MCP 工具面（`pip install 'retinue[mcp]'` 后 `retinue-server mcp`），
不必学 CLI；也可以用 HTTP 加 actor token 接入。

## 众卿需要联网、注册账号吗？会上传数据吗？

服务端使用你管理的本地账号，不要求注册 Retinue 云账号；文件模式与只读公开演示不要求登录。
系统没有内置遥测或强制外发请求，本地运行可以离线。可选 IM 适配与显式配置的 runtime 接入会连接你选择的服务。

## 数据存在哪里？能带走吗？

规范状态落在你指定的数据目录里：文件模式的任务卡，或服务端的 SQLite 数据库与相关状态。
停掉进程后再复制目录，以获得一致备份。运行时源记录与外部成果保留在各自位置，不会自动纳入备份。
详见[数据治理](data-governance.md)与[自托管说明](../SELF_HOSTING.md)。

## 怎么看到同一任务的多智能体协作？

打开任务协作视图，可以查看委派与依赖关系、设备/runtime/模型泳道、模块贡献，以及分支指令、已报进展、
等待对象和成果引用。身份与进展保留来源；模型登记不等于供应商认证，执行者声明完成仍需独立验收。
原生会话观察需要显式任务绑定与结构化回执，才会成为任务进度；用量仅覆盖已上报来源，不是完整供应商账单。
详见 [PRD](PRD.md) 和[当前截图](releases/2026-10-02-collaboration-observability.md)，截图均使用合成数据。

## 怎么跑起来？

需要 Python 3.10 及以上。最快的路径是 Docker Compose：设置 `RETINUE_ADMIN_PASSWORD`（至少八位），
执行 `docker compose up --build`，等中枢在 9219 端口起来后打开 `http://127.0.0.1:9219/`，
以 `operator` 登录。从开卡、认领、回写到验收的完整十分钟走廊写在仓库 README 里。

## 支持哪些聊天平台？

飞书 / Lark 适配已交付，在租户配置允许的前提下实测跑通了 bot 之间的回执传递。企业微信、钉钉、Telegram
与 Slack 在路线图上。IM 适配只把意图带进任务板，它本身永远不是一条独立的命令通道。
