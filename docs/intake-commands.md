# 入站指令语法（M1）

渠道机器人（飞书、电报等）把用户消息哑转发到枢纽
`POST /api/intake/{channel_id}/webhook`（字段：`text`、`sender_id`、`message_id`）。
**指令解析全部在枢纽侧完成**；桥只负责投递与把响应里的中文 `reply` 回聊。

身份仍走通道令牌 + `channel_users` 映射。未映射用户继续收到
`403` + `X-Intake-Error: channel-user-unmapped`，零写入。写操作（进度、备注、完成）
以映射后的板上用户签名，并用 `event_key = intake:{channel}:{message_id}` 做幂等。

## 指令表

| 意图 | 中文 | 英文 | 效果 |
|------|------|------|------|
| 开卡 | `开卡 标题` 或**无前缀**的普通消息 | `new 标题` | 开大厅卡（M0 默认路径，向后兼容） |
| 进度 | `进度 task-YYYYMMDD-NNN 数字 [说明]` | `progress …` | 追加进度事件（0–100）；卡不在 `doing` 时降级为纯备注并在 `reply` 说明 |
| 备注 | `备注 task-YYYYMMDD-NNN 文字` | `note …` | 只追加链上备注 |
| 查询 | `查 task-…` / `状态 task-…` | `status …` | 只读：返回状态、持棒、进度；零写入 |
| 完成 | `完成 task-… [说明]` | `done …` | 转为 `done`；非持棒人收到中文拒绝文案，不写链 |

首行决定意图；多行时其余行并入说明/备注。

## 响应字段

- **开卡**：保留 M0 字段 `task_id`、`status`、`created_by`、`receipt`，并新增 `intent`、`reply`。
- **其余意图**：至少含 `intent` 与可直接转发回聊天的中文 `reply`；写成功时附带 `task_id` / `status` 等摘要。

## 与桥的关系

`tools/feishu_intake_bridge.py` 仍可按 M0 消费开卡回执（看 `task_id` 拼固定文案）。
M1 起枢纽已给出 `reply`，桥可改为优先转发 `reply`，从而覆盖进度/备注/查询/完成
而无需在适配器里解析指令。适配器保持哑转发即可。
电报最小示例：`tools/telegram_intake_bridge.py`（长轮询 getUpdates，运行手册
[`docs/telegram-bridge-runbook.md`](telegram-bridge-runbook.md)）。
