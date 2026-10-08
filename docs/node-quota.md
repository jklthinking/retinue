# 节点模型额度

支持 Claude、Codex、Grok、Cursor、Kimi 订阅额度与 Moonshot API 余额。
xAI management、Anthropic admin、OpenAI admin、Cursor admin 仅保留注册扩展点。

首次安装节点时 `enroll --install` 会列出检测到的 CLI/凭证并询问同意，默认否。
重新安装时不传 `--quota-consent` 会保留已有选择、同意时间和每家的代理配置。
非交互使用 `--quota-consent all|none|claude,codex,...`。
默认仅渲染调度时不写配置，显式传入同意选项则保存选择。
事后使用 `retinue-node quota --enable codex grok`、`--disable grok` 或 `--status`。

本机配置为 `~/.config/retinue/quota.json`，Windows 为
`~/AppData/Roaming/retinue/quota.json`，可用 `RETINUE_QUOTA_CONFIG` 覆盖。
配置原子写入并设为 0600（Windows 仍需部署方设置账户 ACL）。示例：

```json
{
  "enabled_providers": ["codex", "moonshot"],
  "consented_at": "2026-01-01T00:00:00Z",
  "providers": {
    "codex": {"proxy": "http://127.0.0.1:7897"},
    "moonshot": {"key_env": "MOONSHOT_API_KEY", "domain": "cn"}
  }
}
```

每家可指定 `proxy`，省略则使用环境代理；空字符串禁用代理。
Moonshot `domain` 支持 `cn` 与 `ai`，配置只保存环境变量名，不保存 API key。
Codex/Grok 分别尊重 `CODEX_HOME`/`GROK_HOME`。

`retinue-node quota --node sample-node --dry-run` 仅打印 JSON；
`--provider codex` 限定一家。默认使用节点令牌上报 `POST /api/nodes/quota`，
请求体为 `{node, collected_at, providers}`。服务端接口见下文。
每条 provider 含状态、套餐、账号哈希指纹、窗口、余额、读取时间、脱敏错误和来源。
窗口含 key、label、period、used_percent、used、limit、unit、UTC resets_at 与 raw_reset。
百分比均为 0–100 的百分数；Grok Bot 接口的比例会乘 100。

未同意的 provider 不读取凭证或执行 CLI。凭证仅用于本机向固定厂商额度接口鉴权，
不会发送给 Retinue；不发送模型推理请求，我们的代码不调用凭证 / OAuth 刷新接口、不改写凭证，
不输出 token、cookie、邮箱、账号 ID。单家失败不影响其他家。
Kimi 直接在临时空工作目录运行内置 `/usage`，允许 CLI 按正常启动流程续凭证；
若 CLI 先要求信任工作区，仅确认本次创建的临时空目录。
识别欢迎横幅或带框输入行，等待补全菜单并再次回车，不发送提问。
若发现会话创建或模型回复迹象立即退出并报错。
CLI 超时或解析失败后重新读取凭证，仅用未过期 token 查询 API。
Windows 无 pty 时回退 API。
相对重置时间以读取时刻换算，因此 CLI 分钟显示可能造成分钟级误差。

`deploy/systemd/retinue-node-quota.service` 与 `.timer` 是每天一次的用户级样板，
需部署方提供 node.env 中的节点、服务器和令牌文件环境变量，并确保 PATH 能找到 CLI；
本仓库不会安装它们。

## 服务端接口（schema 26）

`POST /api/nodes/quota` 仅接受已准入节点的有效节点令牌，`node` 必须与令牌绑定节点一致。
登录会话与 agent 令牌不能代报。成功返回 `{"status":"ok","report_id":1}`。
服务端仅新增 `quota_reports` 与 `quota_snapshots`，旧表不变；每次写入在同一事务内
清理接收时间超过 90 天的上报及其快照。升级已有数据库需先停止写入并运行数据库迁移。

请求示例（Authorization 使用节点 bearer）：

```json
{"node":"sample-node","collected_at":"2026-10-04T00:00:00Z","providers":[
  {"provider":"claude","kind":"subscription","status":"ok","plan":"pro",
   "account_fp":"abcdef123456","source":"api","fetched_at":"2026-10-04T00:00:00Z",
   "error":null,"balance":null,"windows":[
     {"key":"five_hour","label":"five_hour","period":"5h","used_percent":42,
      "used":null,"limit":null,"unit":"percent","resets_at":"2026-10-04T05:00:00Z","raw_reset":null}
   ]}
]}
```

请求上限 256 KiB（超出 413），每 provider 最多 50 个窗口、每次最多 10 个不同 provider。
provider 白名单来自节点 `PROVIDERS`；status 仅允许 `ok/error/expired/not_configured/consent_missing`。
所有嵌套对象禁止多余字段；凭证形状文本、非法时间、非有限数和越界百分比返回 422，且不落库。
时间必须为带时区的 ISO 时间。字符串有长度上限，账号指纹仅接受 12–64 位十六进制或空值。
错误响应不回显提交的内容。

`GET /api/quota` 接受登录用户或 agent bearer，返回 `generated_at` 和 `providers` 数组。
按 provider + account_fp 合并；空指纹按 provider + node 区分。每组按 fetched_at 取最新，
同时间按写入序号取最新；nodes 列出保留期内观察该账号的所有节点。windows 按 resets_at
升序，空时间在末尾。fetched_at 超过 26 小时为 stale。最新状态为 error/expired 时附
`last_ok: {windows, fetched_at}`，没有成功记录时为 null。

```json
{"generated_at":"2026-10-04T01:00:00+00:00","providers":[
 {"provider":"claude","kind":"subscription","status":"error","plan":"pro",
  "account_fp":"abcdef123456","nodes":["sample-node"],"windows":[],"balance":null,
  "fetched_at":"2026-10-04T00:30:00+00:00","error":"Quota query failed","stale":false,
  "last_ok":{"windows":[],"fetched_at":"2026-10-04T00:00:00+00:00"}}
]}
```

`GET /api/quota?compact=1` 每个 provider 一行，包含 provider、status、window、resets_at、
fetched_at、stale；window 为 used_percent 最大的完整窗口。多账号取最紧窗口所属账号，
百分比相同时取较新账号；无百分比时取最新账号，window/resets_at 为 null。
此视图不携带账号指纹或节点名称，错误状态不自动替换为旧成功数据。

`GET /api/quota/history?provider=claude&days=30` 返回 generated_at、provider、days、history。
days 允许 1–90，包含今天在内的 UTC 日期；每个账号（空指纹则节点）每个窗口 key 每天
取最后一次有 used_percent 的读取，条目含 date、account_fp、node、key、period、
used_percent、fetched_at。无读数的日期不补值，未来读数不进入历史。

节点上报前按 period + resets_at + used_percent 去重，优先保留语义化 key 而非数字索引别名。

## 手动刷新（schema 27）

首页「模型额度」右侧的「刷新查询」会创建一次真实查询请求。节点用自己的令牌
领取请求，只运行本机已同意的采集器，再带 `refresh_request_id` 上报。
单纯重新读取 `/api/quota` 不算完成查询。默认管理员可点击；viewer 只能看数据。
`RETINUE_QUOTA_REFRESH_MEMBERS=1` 可允许 member，`RETINUE_QUOTA_REFRESH=0` 可关闭功能。

- `POST /api/quota/refresh`：登录管理员提交 `{request_key}`，可加白名单 `providers`
  子集和 `nodes` 子集；不接受命令、参数、路径或 URL。
- `POST /api/nodes/quota/refresh/claim`：有效节点令牌提交 `{node}`；无请求时返回 204。
- `GET /api/quota/refresh/{batch_id}`：登录用户或有效 agent 查看脱敏的批次状态。

请求先等待节点，120 秒内无人领取则超时。领取后有 180 秒上报期限；节点采集硬超时
150 秒。只有关联的新上报能完成请求。全部成功、部分成功、失败和超时分别提示，
没有新数据时保留并明确标注上次数据。节点重复领取和终态后迟到的上报不改变终态。
同范围的活跃请求复用；不同范围返回冲突。每节点完成后冷却 120 秒，每 UTC 日最多
48 次。批次成员不可变，多个点击可引用同一个节点请求。

手动运行 `retinue-node quota-poll --node sample-node --url http://127.0.0.1:9219
--token-file NODE_TOKEN_FILE`。自动查询使用新增的
`deploy/systemd/retinue-node-quota-poll.service` 和 `.timer`，沿用 `node.env`。
部署者将样板复制到用户的 systemd 单元目录，确认 CLI/PATH 与本机同意配置后运行
`systemctl --user daemon-reload` 和
`systemctl --user enable --now retinue-node-quota-poll.timer`。
每 30 秒轮询一次，典型等待约 30 秒到 2 分钟。已有每日额度 timer 保留，共用采集锁；
heartbeat、runtimes、sessions 和 live-cycle 的周期均不改变。
这些是部署样板，不会随着安装包自动启用。Windows 可由本机调度器定期运行相同命令。

升级前停止所有写入并做一致性 SQLite 备份，再执行显式 migrate。schema 27 仅新增
`quota_refresh_requests` 和 `quota_refresh_batches`，保留 90 天。
回滚优先关闭开关并停用 poll timer；如恢复旧二进制，必须同时恢复升级前备份，
因为旧版拒绝更高 schema。恢复备份会丢失备份后的写入。已有同意选择不应清空。

首版首页按批次汇总状态，不展开每个节点的倒计时。节点时钟允许与 Hub 相差 120 秒，
查询完成证明用 Hub 接收时间，原始查询时间仍按节点上报显示。Windows 硬超时会
把整批记为失败；成功的中间结果不保留。Windows 分支尚未在真实设备上验证。
