# Coordination bus v0.1 / 协调总线 v0.1

Retinue coordinates the same event through two complementary layers.

Retinue 通过两个互补层表达同一个协作事件。

## IM layer: human-visible, and — where the platform allows — the trigger transport

People create intent, discuss context, and read task receipts in an existing IM channel. Agents emit the canonical receipt whenever a task changes status or holder. On platforms that deliver bot-to-bot messages, the receipt doubles as the machine trigger: the receipt @-mentions the next holder's bot, the target bot parses the receipt, validates the state-machine edge, and claims the task. IM is the social interface and (where supported) the transport; it is never the durable task database.

For server-backed publication, an addressed human message reaches the
deterministic dispatch endpoint with the platform message ID as its idempotency
key. An operator-controlled sender mapping selects an actor-bound server token;
chat membership alone grants no authority. The text is intent only: it cannot
select executable configuration, and unmatched intent receives an actionable
reply without creating a card.

在服务端发布路径中，被 @ 的人类消息携平台消息 ID 作为幂等键进入确定性
dispatch。只有部署方维护的发送者映射才能选中绑定 Retinue actor 的服务端
token；仅仅加入群聊不构成授权。消息正文只作为意图数据，不能选择可执行
配置；没有匹配流程时，适配器会给出可行动的回复且不创建任务卡。

人在已有 IM 群中下达意图、讨论上下文、阅读任务回执。状态或持棒人变化时，agent 发出规范回执。在支持 bot 间收信的平台上，回执同时就是机器触发：回执 @下一棒 agent 的 bot，对方解析回执、校验状态机后接棒。IM 是人际界面，也可以是传输通道，但永远不是持久任务数据库。

### Platform capability matrix / 平台能力矩阵

| Platform | Bot receives bot messages | Notes |
|---|---|---|
| Feishu / Lark | **Yes** (verified in production, 2026-07) | See requirements below. |
| Telegram | No (hard platform limit) | Machine layer falls back to file bus + daemon. |
| Others | Untested | Assume "no" until verified; file bus works everywhere. |

Feishu bot-to-bot requirements, distilled from a working deployment:

1. **One app per agent.** Each agent runs as its own 企业自建应用 with its own `app_id`, subscribing to `im.message.receive_v1` (long connection).
2. **`open_id` is scoped per app.** The same user or bot has a *different* `open_id` under every app. Receiver-side allowlists must list sender identities as resolved under the receiver's own app; sender-side @-mentions must use the target bot's own `open_id`. Keep an explicit mention-resolution map (`display name -> target bot open_id`) per sending app.
3. **Trigger on @-mention only, allowlist senders, never wildcard.** This is both the loop guard and the security boundary: a bot acts only on receipts that @-mention it from an allowlisted sender.
4. **Receipts are terminal for everyone except the addressed next holder.** No bot reacts to a receipt that does not @-mention it; this prevents echo storms.

飞书 bot2bot 的可复现配置（提炼自已上线部署）：①每个 agent 一个独立自建应用，长连接订阅 `im.message.receive_v1`；②`open_id` 按应用隔离——接收方白名单要用"发送方在接收方应用下"的 open_id，发送方 @对方要用"目标 bot 自身"的 open_id，各发送端维护一份名字→open_id 的 mention 映射；③只在被 @ 时触发 + 发送方白名单、禁止通配，兼作防环与安全边界；④回执只对被 @ 的下一棒生效，其余 bot 一律不响应，防止回声风暴。

## Machine layer: file bus (canonical state and fallback)

YAML task cards on a shared file bus are the durable state. Each mutation validates the state-machine edge and appends a chain event. Files may be transported by any deployment-approved synchronization mechanism; Retinue defines the layout, not the transport.

On platforms without bot-to-bot delivery (e.g. Telegram), or when IM is down, coordination degrades to the file bus alone: a per-node daemon watches `tasks/` and triggers the local agent when a card lands on it. A relay may additionally carry events across platforms that cannot observe each other. Whatever the transport, the receiving side validates and applies the task mutation, then emits the human-visible receipt where appropriate.

文件总线中的 YAML 任务卡保存持久状态；每次变更先校验状态机，再追加 chain 事件。文件可使用部署方认可的任意同步方式传输，Retinue 只定义目录与协议，不绑定运输工具。

在不支持 bot 间收信的平台（如 Telegram），或 IM 通道不可用时，协调降级为纯文件总线：各节点 daemon 监听 `tasks/`，任务卡落到本节点 agent 名下即触发。跨平台互不可见时可另加 relay 转运事件。无论走哪条通道，接收方都先校验并应用任务变更，再按需产生面向人的 IM 回执。

```text
human intent in IM
  -> receiving agent
  -> validated YAML mutation on file bus
  -> canonical IM receipt @next-holder bot
       -> (platform supports bot2bot) next bot parses receipt, validates, claims
       -> (otherwise) per-node daemon sees the card land, triggers local agent
  -> ... repeat until terminal state; final receipt @-mentions the human boss
```

One logical transition must have one task-card mutation. Relays and IM adapters should be idempotent so retries do not append duplicate chain events.

The writer for that mutation is always the current task `holder`. Relays transport an already-authorized event; they do not acquire write authority. A handoff is valid only when the current holder records the new holder in the canonical card and emits the matching receipt. MCP update tools reject non-holder callers. File-level conflict detection across synchronization tools is a later protocol extension.

每次变更的写入者必须是当前任务 `holder`。Relay 只转运已获授权的事件，不因此获得写权限。只有当前 holder 在事实卡中写入新 holder 并生成匹配回执，交棒才成立。MCP 更新工具拒绝非 holder 调用；跨同步工具的文件冲突检测留待后续协议扩展。
