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

RETINUE is **source-available**, not OSI-approved open source. RETINUE is licensed under the
PolyForm Noncommercial License 1.0.0. It is free for noncommercial use — personal projects, study,
research, charities, educational and government research institutions — including self-hosting and
modification. Commercial use, such as a hosted service, resale, paid installation for a customer,
or use inside a for-profit organization, requires a separate commercial license from JKL Thinking.
Versions published earlier under FSL-1.1-Apache-2.0 keep the license they shipped with.

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

No. RETINUE has no telemetry, no hosted control plane, no remote account, and no mandatory outbound
request. The hub, board, demo and exporters work offline. Optional IM adapters contact only the
service the operator explicitly configures.

## Where does RETINUE store data, and can I take it away?

Canonical state lives in one operator-chosen directory. Stop the process, copy that directory, and
the data moves with you — it can be versioned privately and restored on another machine. See the
data governance and self-hosting documents in the repository.

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

众卿是**源码可见（source-available）**，不是 OSI 认证的开源。许可证为 PolyForm Noncommercial 1.0.0：
个人、学习、研究、公益、教育与政府研究机构可免费使用，含自托管与修改；任何商业用途（托管服务、转售、
向客户收费部署、或在营利性组织内部使用）需向 JKL 神思记单独取得商业授权。此前以 FSL-1.1-Apache-2.0
发布的版本仍沿用其发布时的许可证。

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

不需要，也不会。众卿没有遥测、没有托管控制面、没有远程账号，也没有任何强制外发请求，中枢、看板、demo
与 exporter 均可离线运行。可选的 IM 适配只会连接你自己显式配置的那个服务。

## 数据存在哪里？能带走吗？

全部规范状态落在你指定的一个目录里。停掉进程、把目录拷走，数据就跟着走，可以私有版本化，也可以在另一台
机器上还原。详见仓库中的数据治理与自托管文档。

## 怎么跑起来？

需要 Python 3.10 及以上。最快的路径是 Docker Compose：设置 `RETINUE_ADMIN_PASSWORD`（至少八位），
执行 `docker compose up --build`，等中枢在 9219 端口起来后打开 `http://127.0.0.1:9219/`，
以 `operator` 登录。从开卡、认领、回写到验收的完整十分钟走廊写在仓库 README 里。

## 支持哪些聊天平台？

飞书 / Lark 适配已交付，在租户配置允许的前提下实测跑通了 bot 之间的回执传递。企业微信、钉钉、Telegram
与 Slack 在路线图上。IM 适配只把意图带进任务板，它本身永远不是一条独立的命令通道。
