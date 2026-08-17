# 飞书入站桥运行手册(M0)

`tools/feishu_intake_bridge.py` 把飞书群/单聊里的用户消息变成板上大厅卡,
并把卡号回执送回会话。它只做协议适配:开卡走 hub 的 intake 接口
(`POST /api/intake/{channel}/webhook`,通道令牌认证),身份映射、幂等、
签名归属全部在 hub 侧完成(v16 起)。

## 组件与数据流

```
飞书平台 ──事件回调──▶ 桥(本地 HTTP 监听,经反代/隧道暴露)
                         │ 1. 校验 verification token
                         │ 2. 归一化消息(open_id / text / message_id / chat_id)
                         ▼
                       hub intake webhook(通道令牌)
                         │ 已映射 → 开大厅卡,签名=映射板上用户
                         │ 未映射 → 403 + X-Intake-Error: channel-user-unmapped
                         ▼
                       桥回执:已映射回卡号;未映射回注册引导文案
                         (应用消息接口优先,群 webhook 兜底)
```

幂等由 hub 保证:`event_key = intake:{channel}:{message_id}`,飞书重投不会
重复开卡。

## 部署步骤

1. **hub 侧准备**(管理员,一次性):
   - 建通道令牌:`POST /api/admin/channel-tokens {"channel_id": "feishu"}`,
     把返回的令牌写入部署机的 `<data-dir>/feishu-channel.token`(0600)。
   - 建身份映射:`PUT /api/admin/channel-users`,把每个允许开卡的飞书
     `open_id` 绑定到板上 actor。未映射用户只会收到注册引导文案。
2. **复制配置模板**:`examples/feishu-bridge.example.yaml` →
   `<data-dir>/feishu-bridge.yaml`,按注释填 env 变量名或文件路径。
   仓库内不存任何真实凭据;所有秘密字段都是 `*_env` / `*_file` 间接引用,
   由中枢/运维在部署时注入环境或落盘。
3. **自检**:`python tools/feishu_intake_bridge.py --config <path> --check-config`
   打印脱敏后的解析结果(含 `reply_mode`)。
4. **模拟冒烟**(不出网):`--simulate event.json` 注入一条模拟事件,确认
   输出 `{"status": "opened", ...}`。
5. **运行**:`python tools/feishu_intake_bridge.py --config <path>`,监听
   `listen.host:listen.port`(默认仅回环)。用反代或隧道把
   `POST /feishu/events` 暴露给飞书平台;`GET /healthz` 供探活。

## 飞书控制台配置清单

需要一个**企业自建应用**:

- **事件订阅**:请求地址指向桥暴露的回调 URL;订阅事件
  `im.message.receive_v1`(接收消息)。保存后飞书会发
  `url_verification` 挑战,桥自动应答——因此先起桥再填地址。
- **权限**(回执走应用消息接口时需要):`im:message`(发送消息),
  以及按发送方式需要的 `im:message:send_as_bot`。仅做机器人所在群的
  回话,不需要读取类权限。
- **机器人能力**:启用机器人,把它拉进目标群;群内 @机器人 或单聊均可,
  `@_user_N` 提及占位符会被剥掉,不进卡面。
- **凭据形态**:
  - `app_id`(非密)可写在配置里;
  - `app_secret`、`verification_token` 只能经环境变量或文件注入
    (`app_secret_env` / `verification_token_env`,或对应 `_file`)。

## 凭据与降级说明

回执通道由凭据自动决定(`reply_mode`):

| 凭据形态 | 入站(收消息) | 回执送达 |
| --- | --- | --- |
| 应用凭据齐全(app_id + app_secret + 事件订阅) | 可用 | 应用消息接口,精确回到来源会话 |
| 只有应用事件订阅,无 app_secret | 可用 | 降级:仅记日志,用户收不到回执 |
| 只有群自定义机器人 webhook | **不可用** | webhook 只能向其所在群发消息;它收不到事件,入站无从谈起 |
| 两者皆无 | 不可用 | 桥启动时明确告警 `reply mode: none` |

要点:**群 webhook 是送达通道,不是入站通道**。只有 webhook 的部署里,
hub 侧的飞书通知(`RETINUE_FEISHU_WEBHOOK` 出站回执)照常工作,但用户
消息开卡不可用——这属于凭据缺失,不是桥故障。补应用凭据后入站即恢复,
无需改代码。

## 安全边界

- 桥只认部署配置里的凭据;卡面文本、消息内容一律视为不可信,永不执行。
- 回调校验 `verification_token`;不配则监听端只能靠反代侧保护,生产
  环境必须配置。
- 秘密永远不进仓库、不进日志;`--check-config` 输出已脱敏。
- 卡面只保留一行标题与 200 字符消息摘要(含 `message_id` 回链锚点),
  不复制整段会话内容。
