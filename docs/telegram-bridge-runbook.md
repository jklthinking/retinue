# 电报入站桥运行手册

`tools/telegram_intake_bridge.py` 用 Telegram Bot API 的 `getUpdates` 长轮询
收取文本消息，归一化后哑转发到枢纽入站接口
(`POST /api/intake/{channel}/webhook`，通道令牌认证)，再把响应里的中文
`reply` 原样回发到来源聊天。指令解析、身份映射、幂等与写链全部在枢纽侧完成；
桥零业务逻辑。自托管场景不需要公网回调地址，比 webhook 门槛低。

与飞书桥对照见 [`docs/feishu-bridge-runbook.md`](feishu-bridge-runbook.md)；
入站指令与 `reply` 约定见 [`docs/intake-commands.md`](intake-commands.md)。

## 组件与数据流

```
Telegram ──getUpdates 长轮询──▶ 桥(本机进程)
                                 │ 1. 可选聊天白名单过滤
                                 │ 2. 归一化(text / sender_id=用户数字 id /
                                 │    message_id=update_id)
                                 ▼
                               hub intake webhook(通道令牌)
                                 │ 已映射 → 枢纽解析指令,返回中文 reply
                                 │ 未映射 → 403 + X-Intake-Error: channel-user-unmapped
                                 ▼
                               桥回执:优先原样转发 reply;未映射回注册引导文案
                                 (Bot API sendMessage)
```

幂等由枢纽保证:`event_key = intake:{channel}:{message_id}`。桥把
`update_id` 当作 `message_id`，进程崩溃后用同一 offset 重拉时不会在枢纽侧
重复写链。

## BotFather 建机器人拿令牌

1. 在 Telegram 里打开 `@BotFather`，发送 `/newbot`，按提示设显示名与用户名。
2. BotFather 返回的 bot token **只**写入部署机环境变量(例如
   `RETINUE_TG_BOT_TOKEN`)或权限收紧的本地文件;仓库、配置模板、日志里
   永远不出现令牌本体。
3. 按需用 `/setprivacy` 等调整群内可见性;私聊与被 @ 的群消息均可作为入站。
4. 把机器人拉进目标群,或先私聊机器人发一条消息以建立会话。

## 通道令牌申请

管理员在枢纽侧一次性准备:

1. 建通道令牌:`POST /api/admin/channel-tokens`，body 含
   `{"channel_id": "telegram"}`(或你选用的 channel_id),把返回令牌写入
   部署机 `<data-dir>/telegram-channel.token`(建议 0600)。
2. 配置里用 `channel_token_file` 或 `channel_token_env` 间接引用,不要把令牌
   粘进 YAML。

## 用户映射

每个允许操作的人需要一条 `channel_users` 映射:

- `channel_id`: 与桥配置一致(默认 `telegram`)
- `channel_user_id`: Telegram **数值用户 id**(十进制字符串),不是 @username
- `actor_id`: 板上已有 actor

未映射用户只会收到注册引导文案,枢纽零写入。获取用户 id 可让对方先给机器人
发消息,从桥日志里的 `unmapped sender …` 读取,或用临时探测工具;不要把真实
id 写进仓库。

可选 `telegram.allowed_chat_ids`:只处理名单内的 `chat.id`,降低误拉群噪声。

## 部署步骤

1. **复制配置模板**:`examples/telegram-bridge.example.yaml` →
   `<data-dir>/telegram-bridge.yaml`,按注释填 env 变量名或文件路径。
2. **注入秘密**:设置 `RETINUE_TG_BOT_TOKEN`(或你在 YAML 里写的名字),并落盘
   通道令牌文件。
3. **自检**(不联网):  
   `python tools/telegram_intake_bridge.py --config <path> --check-config`  
   打印脱敏后的解析结果;`channel_token` / `bot_token` 应为 `***`。
4. **运行**:激活与枢纽相同的 Python 环境后  
   `python tools/telegram_intake_bridge.py --config <path>`  
   进程会阻塞在 `getUpdates` 长轮询;用 systemd/supervisor 保活即可。
5. **冒烟**:映射用户向机器人发送 `开卡 试一条`,应收到枢纽返回的中文
   `reply`;未映射用户应收到注册引导。

## 常见问题

| 现象 | 排查 |
| --- | --- |
| `--check-config` 报 bot_token_env unset | 部署机未导出 YAML 里写的环境变量名 |
| 一直无入站 | 确认进程在跑;群内若开启隐私模式,需 @机器人或关掉隐私;检查白名单是否误杀 |
| 每人只收到注册引导 | `channel_users` 未登记,或登记的不是数值 user id |
| 枢纽 401/403(非 unmapped) | 通道令牌错误或 channel_id 与令牌不一致 |
| 开卡成功但聊天无回复 | 看桥日志是否 `sendMessage failed`;机器人是否被踢出群/用户是否停用机器人 |
| 想换公网 webhook | 本示例刻意用长轮询;若改 webhook 需另暴露 HTTPS,不属于本最小示例范围 |

## 安全边界

- 桥只认部署配置里的凭据;消息正文视为不可信,永不执行。
- 机器人令牌与通道令牌只经 `*_env` / `*_file` 注入;`--check-config` 已脱敏。
- 可选聊天白名单默认关闭(空列表=全部放行);生产群聊建议显式配置。
- 不向日志打印令牌、完整 `bot_api` URL 中的密钥段,或用户消息全文以外的敏感头。
