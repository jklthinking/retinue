# Walkthrough: bot-to-bot task relay over Feishu / 飞书 bot2bot 任务接力实录

A reproduction guide distilled from a real production run (2026-07-19). Two agents on the
same node relayed a task card through a Feishu group in **~50 seconds end to end**, with the
task card as the only source of truth. Follow this and you should get the same result.

本文提炼自一次真实生产运行（2026-07-19）：两个 agent 经飞书群完成一次任务接力，全程约
50 秒，状态唯一以任务卡为准。照做即可复现。

## Cast / 角色

| Placeholder | Role |
|---|---|
| `agent-a` | Dispatcher bot. Its own Feishu 企业自建应用 (`APP_A`). Here: a Claude Code instance behind an IM bridge. |
| `agent-b` | Worker bot. Its own app (`APP_B`). Here: a Codex instance behind the same kind of bridge. |
| `boss` | The human. Gets the final @-mention. |
| `oc_GROUP` | A group chat containing both bots (and the boss). |
| `ou_AGENT_B` | agent-b's **own** bot open_id — the id you @-mention it with. |

## Prerequisites / 前置条件

1. **One app per agent.** Each agent registers its own 企业自建应用 with a bot, and
   subscribes to `im.message.receive_v1` over a long connection.
2. **Minimal scopes are enough.** The run used: `im:message` (send),
   `im:message.group_at_msg:readonly` (receive @-mentions). Notably **not** needed:
   `im:message.group_msg` (read full history) — the protocol never reads IM history,
   because state lives in task cards, not chat.
3. **Receiver allowlist.** agent-b's bridge only reacts to @-mentions from allowlisted
   sender ids. Add agent-a's identity *as resolved under agent-b's app* (open_id is scoped
   per app — the same principal has a different open_id under every app).
4. **Sender mention map.** agent-a keeps a map `display name -> ou_AGENT_B` using
   agent-b's **own** bot open_id; this id works across apps in @-mentions.
5. **Loop guards.** Bots trigger on @-mention only (not on all group traffic); receipts are
   ignored by every bot except the @-mentioned next holder.

## The run, step by step / 逐步实录

**Step 1 — dispatcher creates the card** (durable state first, IM second):

```bash
retinue task new tasks/ --id task-20260719-002 \
  --title "bot2bot walkthrough: reply and claim via IM receipt" \
  --created-by agent-a --holder agent-b --dept engineering \
  --note "T0 live test: relay receipt over Feishu"
retinue receipt tasks/task-20260719-002.yaml   # prints the canonical receipt text
```

**Step 2 — dispatcher sends the receipt, @-mentioning the next holder** (as its own app,
`POST /open-apis/im/v1/messages?receive_id_type=chat_id`):

```json
{
  "receive_id": "oc_GROUP",
  "msg_type": "text",
  "content": "{\"text\": \"<at user_id=\\\"ou_AGENT_B\\\">agent-b</at> 【任务回执】task-20260719-002 ...\\n状态：— → queued　持棒：— → agent-b　备注：...\\n—— 接棒指令：①回群确认 ②retinue task update ... --status doing ③贴回执\"}"
}
```

Sent 21:54:00, API `code: 0`. The message carries both the machine-parseable receipt and a
short instruction block for the receiving agent.

**Step 3 — worker claims.** agent-b's `im.message.receive_v1` fired on the @-mention; its
bridge passed the message to the agent, which ran:

```bash
retinue task update tasks/task-20260719-002.yaml \
  --status doing --who agent-b --note "claimed via feishu bot2bot receipt"
```

Card observed mutated at 21:54:50 — **~50 s from send to claim**, chain appended:

```yaml
- who: agent-b
  did: claimed via feishu bot2bot receipt
  from_status: queued
  to_status: doing
```

**Step 4 — dispatcher verifies and closes**, final receipt @-mentions the human:

```bash
retinue task update tasks/task-20260719-002.yaml --status done --who agent-a \
  --note "verified: bot2bot loop closed in ~50s"
```

Then one more group message: `<at user_id="ou_BOSS">boss</at> 【任务回执】... doing → done`.
Sent 21:55:43. Loop closed: **card → receipt @bot → claim → card → receipt @human**.

## What made it work / 成败关键复盘

- **open_id scoping is the #1 pitfall.** Allowlists use sender ids under the *receiver's*
  app; @-mentions use the target bot's *own* id. Mixing these up looks like "bot2bot doesn't
  work" when it's just a wrong id.
- **State never lives in IM.** The worker didn't parse chat history; it acted on one
  @-mention and mutated the card. If IM drops, the file-bus daemon path picks up the same
  card — no divergence possible.
- **The receipt doubles as protocol.** The same text humans read in the group is what the
  next bot parses. One format, two audiences.
- **Reading chat history is not required** — and better left unauthorized: it keeps each
  bot's permission surface minimal.

## Latency expectation / 时延预期

@-mention event delivery is near-instant; the ~50 s here was dominated by the receiving
agent's own reasoning/act cycle. Budget "seconds to low minutes" per hop depending on the
worker runtime, not on Feishu.
