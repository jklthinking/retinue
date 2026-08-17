# A2A agent card discovery / A2A 代理卡发现

The server can publish an Agent Card — the discovery document of the
agent-to-agent interoperability standard (Linux Foundation governance since
2025, stable at version 1.0 in 2026) — at the well-known path
`/.well-known/agent-card.json`. This document records what is published, why
each field is safe, and where the two vocabularies do not line up.

服务器可以在约定路径 `/.well-known/agent-card.json` 发布 Agent
Card（代理间互操作标准的发现文档；该标准 2025 年起由中立基金会治理，2026
年发布 1.0 稳定版）。本文档记录发布了什么、每个字段为什么安全，以及两套
术语对不齐的地方。

## The switch / 开关

The card is **off by default**. A private control plane must not start
advertising itself because it was upgraded. An operator turns it on with an
environment variable read per request (no restart beyond setting it):

该卡**默认关闭**。私有控制面不应因为升级就开始对外自我宣传。操作员通过
环境变量开启（按请求读取，设置后无需额外重启）：

```bash
export RETINUE_AGENT_CARD=1   # truthy: 1, true, yes, on
```

Unset or any other value, the route answers 404 and the server is exactly as
it was. Enabling the card widens no other route; the route table is pinned by
`tests/test_route_table.py` and the widening check by `tests/test_agent_card.py`.

未设置或取其他值时，该路由返回 404，服务器行为与之前完全一致。开启此卡
不会放宽任何其他路由；路由表由 `tests/test_route_table.py` 固定，放宽检查
由 `tests/test_agent_card.py` 固定。

## Identity decision: one card for the deployment / 身份决策：整个部署一张卡

A card describes *an* agent, and this server hosts many actors. We publish
**one card for the deployment** listing the skills of the registry, and no
per-actor cards. Reasons:

一张卡描述*一个*代理，而本服务器承载多个行为体。我们选择**为整个部署发布
一张卡**，列出注册表中的技能，不发布按行为体划分的卡。理由：

- The well-known path serves exactly one card per origin; per-actor cards
  would need per-actor URL space that this order does not create.
- Actors are internal baton holders with no individual public endpoint; a
  card per actor would promise an addressable agent that does not exist.
- Per-actor cards would publish the roster — ids, and indirectly nodes and
  online patterns — which is exactly the fleet-mapping material rule 4 keeps
  private. One aggregate card publishes none of it.

What a client can do with the card: learn that this origin is agent-capable,
read the skill catalogue (names, descriptions, category tags) to decide
whether the deployment is relevant, and see from the all-false `capabilities`
and the absent `supportedInterfaces` that no A2A task endpoint is offered.
The next step is contacting the operator out of band — not submitting a task.

- 约定路径每个源只服务一张卡；按行为体发卡需要本工单未创建的 URL 空间。
- 行为体是内部的接力持有者，没有各自的公开端点；按行为体发卡等于承诺一
  个不存在的可寻址代理。
- 按行为体发卡会公开名册——id，并间接暴露节点与在线规律——这正是规则 4
  要求保密的舰队测绘材料。聚合卡不含任何此类信息。

客户端能用这张卡做什么：得知该源具备代理能力，阅读技能目录（名称、描
述、分类标签）以判断此部署是否相关，并从全为 false 的 `capabilities` 与
缺失的 `supportedInterfaces` 看出这里不提供 A2A 任务端点。下一步是带外
联系操作员，而不是提交任务。

## What is published, field by field / 逐字段说明发布内容

The card is built from the skills registry that already exists; there is no
second registry. Every field is defended below.

卡由现有技能注册表构建，没有第二个注册表。逐字段辩护如下：

| Field | Source | Why it is safe |
| --- | --- | --- |
| `name` | Operator-set site label, falling back to the constant `retinue` | The operator chose it for the already-public login screen. |
| `description` | Static constant | Written for this document; contains no deployment data. |
| `version` | Server version string | Already disclosed by unauthenticated `GET /api/health`. |
| `capabilities` | Constants: `streaming`, `pushNotifications`, `extendedAgentCard` all `false` | Honest absence of capability; reveals nothing. |
| `defaultInputModes` / `defaultOutputModes` | Constant `["text/plain"]` | Required by the card shape; a media-type constant. |
| `skills[].id` / `skills[].name` | Enabled-skill names from the registry | Operator-curated registry text, same content the panel shows signed-in users; enabled skills only. |
| `skills[].description` | Registry text | Same operator-curated text. |
| `skills[].tags` | The skill's category, as a one-element list | A category label, nothing more. |

Deliberately absent, with reasons:

有意省略的字段及原因：

- `supportedInterfaces`, `url`, `protocolVersion`: this deployment serves no
  A2A task endpoint, and a card must not promise one. Publishing an endpoint
  we do not serve would be worse than publishing nothing — and any URL would
  also have to carry an address, which rule 4 forbids on this surface.
- `provider`, `documentationUrl`: would require an organisation name or an
  external URL; neither exists that is safe to print.
- `securitySchemes` / `security`: there is no A2A endpoint to protect, so
  there is nothing to declare.
- Anything actor-derived (ids, display names, runtimes, models, last-seen,
  online status), node-derived (node ids, runtime inventories), counts of
  actors or nodes, skill owners, skill source, timestamps: roster and
  topology material. A stranger may learn *what this deployment can do*,
  never *who or where the fleet is*.

- `supportedInterfaces`、`url`、`protocolVersion`：本部署不提供 A2A 任务端
  点，卡不能许诺不存在的端点；且任何 URL 都必须带地址，而规则 4 禁止在此
  表面出现地址。
- `provider`、`documentationUrl`：需要组织名或外部 URL，均无可安全公开的
  值。
- `securitySchemes` / `security`：没有需要保护的 A2A 端点，无可声明。
- 一切行为体派生信息（id、显示名、运行时、模型、最后在线、在线状态）、节
  点派生信息（节点 id、运行时清单）、行为体或节点计数、技能所有者、技能
  来源、时间戳：均属名册与拓扑材料。陌生人可以知道*这个部署会做什么*，绝
  不能知道*舰队有谁、在哪里*。

## Vocabulary mapping, honestly / 如实的术语映射

This order is discovery only: no task submission, no streaming, no push
notification. So the mapping below is written down, not implemented — the
card represents no tasks at all. It is recorded so a future compatibility
layer starts from an honest fit rather than a forced one.

本工单只做发现：无任务提交、无流式、无推送。因此下面的映射只记录不实
现——卡中根本不含任务。记录下来是为了让将来的兼容层从如实的对应出发，
而不是强行套用。

The standard's task lifecycle has seven states; this project has six
(`docs/protocol/task-state.md`):

该标准的任务生命周期有七个状态；本项目有六个（见
`docs/protocol/task-state.md`）：

| Retinue | Standard | Fit |
| --- | --- | --- |
| `queued` | `submitted` | Clean: work accepted, not yet started. |
| `doing` | `working` | Clean. |
| `handoff` | (no distinct state) | A holder change inside active work. The standard has no baton/holder concept, so a handoff is still `working` — the baton itself is unrepresentable. |
| `blocked` | closest to `input-required` | Approximate only; see below. |
| `done` | `completed` | Clean. |
| `cancelled` | `canceled` | Clean. |

Two of the standard's seven states have no equivalent here, which is worth
writing down (this mirrors the audit, `docs/design/audit-2026-08.md`):

标准的七个状态中有两个在这里没有对应，值得记录在案（与审计
`docs/design/audit-2026-08.md` 一致）：

- **`rejected`**: no terminal state for an assignee declining work. Today
  only cancellation exists; a declined card goes back to `queued` or is
  cancelled, and neither means "the assignee refused".
- **`input-required`**: semantically distinct from `blocked`. Waiting on a
  human reply is not the same as being externally obstructed; Retinue's
  `blocked` covers both and cannot tell them apart.

Conversely, the standard's `failed` has no terminal counterpart either —
Retinue cards never terminally fail; they return to `queued` or sit in
`blocked` — but `failed` maps loosely onto that rework loop if a mapping is
ever needed.

- **`rejected`**：没有表示被指派者拒绝任务的终态。目前只有取消；被拒绝的卡
  回到 `queued` 或被取消，二者都不表示"执行者拒绝"。
- **`input-required`**：与 `blocked` 语义不同。等待人类回复不等于被外部阻
  塞；Retinue 的 `blocked` 两者都涵盖，无法区分。

反过来，标准的 `failed` 在这里也没有终态对应——Retinue 的卡永远不会终态
失败，只会回到 `queued` 或停在 `blocked`——但若将来需要映射，`failed` 可
以勉强对应这个返工循环。

The deeper mismatch is the baton against the task lifecycle. The standard's
task is a client-driven message stream with artifacts; a Retinue card is an
append-only chain passed between single holders, one writer at a time, with
an approval gate. The discovery card does not attempt to represent tasks, so
no bad fit is shipped.

更深的不匹配是接力棒与任务生命周期的差异。标准的任务是由客户端驱动的消
息流，带工件；Retinue 的卡是单持有者之间传递的只追加链条，同一时刻只有
一个写入者，并有审批闸。发现卡不试图表示任务，因此没有发布任何牵强对
应。
