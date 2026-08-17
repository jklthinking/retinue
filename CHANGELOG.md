## Unreleased

### Legal

- Relicensed from FSL-1.1-Apache-2.0 to PolyForm-Noncommercial-1.0.0.
  Noncommercial use stays free; all commercial rights are reserved to
  JKL Thinking. Versions already published under FSL keep their terms.
- Documented the Chinese name 众卿 in the README and NOTICE.


### Webui: stale-while-revalidate cache for heavy lists
- Skills and collaboration progress read from `localStorage` first, then
  revalidate. A failed fetch keeps the last good payload instead of an
  empty page. Sidebar refresh bypasses the cache.

### Webui: board data refreshes daily, plus a manual control
- Short polling (8s / 30s) on the home, board, inbox, affairs, sessions,
  workroom, and infra views is now 24 hours. Sidebar refresh reloads the
  signed-in session and every listening view on demand.

### Tools: Telegram intake adapter example — channel-agnostic relay
- New `tools/telegram_intake_bridge.py` long-polls Bot API `getUpdates` (stdlib
  urllib only), normalizes text messages to hub intake fields, and forwards
  the M1 Chinese `reply` (or the unmapped registration guide) back via
  `sendMessage`. Optional chat allowlist; secrets only via env/file
  indirection; `--check-config` stays offline.
- Docs: `docs/telegram-bridge-runbook.md`, template
  `examples/telegram-bridge.example.yaml`. Coverage in
  `tests/test_telegram_bridge.py` (task-20260814-005).

### Server: intake command grammar M1 — in-chat progress, note, status, done
- `POST /api/intake/{channel}/webhook` parses the first line of `text` for
  开卡/new (default M0 open), 进度/progress, 备注/note, 查|状态/status, and
  完成/done. Writes are signed by the mapped board user; unmapped senders keep
  the 403 + `X-Intake-Error: channel-user-unmapped` path with zero writes.
- Progress on a non-`doing` card degrades to a chain note with an explanatory
  `reply` (no 422 to the chat). Non-holders who declare 完成 get a friendly
  Chinese refusal instead of a bare server error. Write intents reuse
  `intake:{channel}:{message_id}` event keys for idempotent retries.
- Responses add `intent` + Chinese `reply` for every intent; the open path keeps
  M0 fields (`task_id`/`status`/`created_by`/`receipt`). Docs in
  `docs/intake-commands.md`. Coverage in `tests/test_intake_cmd_m1.py`
  (task-20260814-004).

### Server: skill self-report M0 — node-side inventory sync
- `POST /api/skills/sync` lets an authenticated node/agent push a bounded skill
  list (upsert-only). Identical name plus normalized snapshot/content hash returns
  `unchanged`; changes refresh via the existing runtime import path and provenance.
  Missing skills are not deleted in M0. Coverage in `tests/test_skills_sync_m0.py`
  (task-20260813-037).
### Server: static board snapshot M0 — offline JSON/HTML fallback
- New `server/snapshot.py` writes deterministic `<data-dir>/snapshots/board.json`
  and `board.html` from active task rows (id/title/status/holder/progress/priority
  plus status counts). `generated_at` reuses the latest task `updated_at` so a
  no-op re-run is byte-stable; the command does not mutate the database.
- CLI: `python -m server.main --data-dir DIR snapshot`. Docs in
  `docs/snapshots.md`. Coverage in `tests/test_snapshot_m0.py`
  (task-20260813-035).
### Server + Webui: node health watermarks M0 — thresholds and idempotent ops cards
- New `server/watermarks.py` classifies heartbeat disk (and optional load) into
  ok / warn / high / critical / unknown using `<data-dir>/watermarks.yaml`
  (default `enabled: false`; template in `docs/examples/watermarks.yaml`).
  Disk defaults match the former alert script: warn ≥80, high ≥90, critical ≥95.
- After a heartbeat writes `disk_json`, the route evaluates watermarks and may
  open one open-dispatch ops card per node and tier (`high` / `critical`) with
  stable refs; warn only logs. Missing watch actor records the level without
  interrupting the heartbeat. `GET /api/nodes` and the heartbeat 200 body both
  expose `watermark`. Infra shows 正常 / 注意 / 告警 / 危急. Coverage in
  `tests/test_watermarks_m0.py` (task-20260813-034).
### Server: QC completion hook M0 — fire-and-forget notify on done
- New `server/qc_hook.py` posts a short JSON payload (`task_id`, `title`,
  `holder`, `done_at`) to an external QC bot when a card first reaches
  `done`. Config lives in `<data-dir>/qc_hook.yaml` (example under
  `docs/examples/qc_hook.yaml`); default is off, and the webhook URL is
  read from an environment variable named in that file.
- `engine.update_task` invokes the hook after `db.flush` on a transition
  into `done`, wrapped so hook failures never change the completion
  result. Idempotency key `qc:{task_id}` prevents re-POST on repeat
  triggers. Coverage in `tests/test_qc_hook_m0.py` (task-20260813-036).

### Changed
- Default copy for human-approval gates no longer uses court titles. Cards, chain notes, and protocol docs say 审批门 / 待拍板; the pipeline enum remains `queen` for compatibility.

### Server: distillation pipeline M0 — candidates, cooling gate, approval promotion
- Schema v19 adds `distill_candidates` and `distill_events`. External executors register bounded summaries via `/api/distill/candidates`; promote requires the 24h cooling gate plus a privileged human session and creates a `knowledge_sources` row (`kind=distill`). Reject leaves an audit note. Protocol iron laws live in `docs/distill-protocol.md`. Coverage in `tests/test_distill_m0.py` (task-20260813-033).
### Server: notifier plugin layer M0 — dual channels, idempotent deliveries, refresh hook
- New `server/notify.py` unifies outbound notification behind a plugin
  interface with three channels: `group_webhook` (legacy custom-bot behaviour
  via `RETINUE_FEISHU_WEBHOOK`), `tenant_app` (Feishu tenant token + IM send,
  injectable HTTP client), and `log` fallback. Deployment config lives in
  `<data-dir>/notify.yaml`; the tree ships `docs/examples/notify.yaml` with
  env-var secret indirection only.
- Schema v20 adds `notification_deliveries` (unique `dedupe_key`, channel,
  target, status, attempts, `message_ref` for later refresh). Repeated
  triggers with the same key do not re-send terminal rows. v19 is reserved
  for parallel distill work, so this branch jumps 18 → 20.
- `refresh_after_approval` looks up a prior delivery by `message_ref` /
  `dedupe_key` and calls the channel refresh hook; tenant-app refresh is a
  recorded stub in M0. Existing `notify_feishu` and Feishu card posts now
  route through the group-webhook plugin without changing external behaviour.
  Coverage in `tests/test_notify_m0.py`. This ships task-20260813-032.

### Server + Webui: queen inbox M0 — four-lane aggregation and daily digest
- New `GET /api/inbox` (`server/routers/inbox.py`, aggregation in
  `server/inbox.py`) folds the four attention lanes into one authenticated
  read: pending decisions (queen-gate approvals), QC comments still waiting
  for a reply, blocked cards with their reason, and in-flight cards that are
  overdue or whose lease heartbeat is lost — each with a count plus the first
  rows. No schema migration; lane predicates reuse the summary read model's
  building blocks.
- The daily digest reuses the reminder delivery facility untouched: the inbox
  GET nudges registration of one `reminder_deliveries` slot per owner account
  with a date-keyed `delivery_key` (`inbox-digest:<user>:<date>`), anchored
  to a deterministic per-user todo whose title carries the day's four lane
  counts, and the existing scanner (already driven by the reclaim/ready sweep
  cadence) delivers it through the `in_app` channel — so a same-day rescan
  neither re-registers nor re-delivers. Deployments without an enabled
  `reminders.yaml` stay fully inert.
- The workbench home page renders a new inbox swimlane above the action
  queue, with lane names shipped as new `ThemeVocab` keys in both the neutral
  and court presets; every row deep-links into its card. Coverage:
  `tests/test_inbox.py` (lane counts, stale reasons, digest date-keying and
  delivery idempotency) and `webui/src/__tests__/inbox.test.tsx` (both
  themes, click-through, retry degradation). This ships task-20260813-031.

### Tools: Feishu inbound bridge M0 — user message opens a hall card, receipt back
- New daemon `tools/feishu_intake_bridge.py`: normalizes Feishu
  `im.message.receive_v1` callbacks (challenge handshake, verification-token
  check, bot/non-text noise ignored, `@_user_N` mentions stripped) and posts
  them to the hub intake webhook with the channel token. Two event sources:
  a loopback HTTP listener for a reverse proxy or tunnel, and `--simulate`
  JSON injection for offline end-to-end tests.
- Receipt delivery is credential-gated: the app message API (cached tenant
  token, addressed to the originating chat) when app credentials exist, a
  group custom-bot webhook as fallback, and an explicit log-only degraded
  mode otherwise. Unmapped senders get the registration guide; the hub now
  marks that refusal with `X-Intake-Error: channel-user-unmapped` so bridges
  can tell "send guidance" apart from real failures.
- Deployment config (`feishu-bridge.yaml` in the data directory; repository
  carries only `examples/feishu-bridge.example.yaml`) holds no secrets:
  every credential is an `*_env` / `*_file` indirection. Deployment steps,
  console checklist, and the webhook-only degradation story live in
  `docs/feishu-bridge-runbook.md`. Server slice of task-20260813-017.
### Server: reminder delivery channels M0 — due scanner, in-app and webhook
- New `server/reminders.py` scans due `reminder_deliveries` slots (schema v18,
  no migration) and fans them out through plugin channels. Deployment config
  lives only in `<data-dir>/reminders.yaml`; the tree ships
  `docs/examples/reminders.yaml` plus `docs/reminders-delivery.md`.
- Channels: `in_app` appends owner-visible todo events; `webhook` POSTs JSON
  (default body is title + scheduled_for; `detail_level: detail` adds notes
  and ids). Timeouts and retries are capped; exhausted slots become
  `abandoned` with audit events. Rescans never re-send a terminal slot.
- The scan function follows the dispatch calendar shape (due query + idempotent
  side effect) and is invoked from the existing reclaim / ready sweep cadence
  without modifying `dispatch_v2.py`. Coverage in `tests/test_reminders_m0.py`
  exercises the fake clock, webhook HTTP stub, idempotency, and retry limit.
  This ships task-20260813-016.

### Webui: personal affairs hub M0 — 我的事务 first screen
- New 「我的事务」view (sidebar entry plus a home-page header button, hidden
  for viewer accounts) aggregating five lanes served by the existing todo
  endpoints: pending proposals, due today, overdue, waiting-on-others, and
  items promoted onto the shared board (which deep-link into their card).
- Proposal confirm/reject (with an optional note) and item complete/snooze
  run through `POST /api/todos/proposals/{id}/confirm|reject` and
  `POST /api/todos/{id}/complete|snooze`; every action is a native button or
  inline form (keyboard reachable), failures surface as `role=alert`, and
  every lane renders an explicit empty state.
- All flavour wording ships as new `ThemeVocab` keys in both the neutral and
  court presets instead of literals inside the view. Vitest coverage renders
  the aggregate lanes in both themes and exercises the confirm, reject, and
  complete flows. Frontend slice of task-20260812-003.

### Server: private todo hub M0 — ownership, proposals, reminders, backlinks
- Schema v18 adds `todo_proposals`, `todo_items`, `todo_events`,
  `reminder_deliveries`, and `todo_task_links`, plus a per-user
  `todo:propose` grant list. Private todos are not shared board cards.
- Ownership is fail-closed: the owner user is the only default reader and
  writer. Viewers are refused on every todo route. Administrators do not
  inherit read access; a compliance GET must carry an explicit `reason` and
  appends an `admin_access` event. Agents may submit proposals only after
  that owner grants `todo:propose`, and they cannot read another person's
  confirmed items.
- Flow: proposal (source-session backlink + dedup key) → owner confirm →
  TodoItem. Complete, cancel, and snooze append events. Reminder slots are
  idempotent on `(todo, scheduled_for, channel)`; this batch exposes
  registration and a due-query, not a delivery channel.
- An owner can promote a private todo into a shared Task. The new card
  keeps the todo id in `refs` and `todo_task_links` holds the reverse
  pointer. Home aggregates pending proposals, due today, overdue, and
  waiting-on-others. This ships the backend slice of task-20260812-003.

### Server: intake protocol M0 — channel identity, publish spec, enroll
- New channel credential (`channel_tokens`, bearer prefix `rtc`): a channel
  token has exactly two capabilities — open a card and read the cards its
  own channel opened. It cannot claim, write another card, dispatch, or
  reach any admin route; the gate is centralized in `require_auth`.
- Channel-user mapping table (`channel_users`): an administrator binds a
  channel-internal user identity (e.g. a Feishu open_id) to a board actor.
  Channel cards are signed by the mapped user, opened as open dispatch, and
  carry the provenance in `tasks.source_channel` / `tasks.source_user` plus
  the first chain event payload; the first note holds the original message
  digest with its platform backlink anchor.
- Generic inbound webhook adapter skeleton (`POST
  /api/intake/{channel_id}/webhook`): normalizes one channel message into
  one hall card, idempotent per platform message id, with an optional
  per-channel shared-secret placeholder (`RETINUE_INTAKE_<CHANNEL>_SECRET`)
  for deployments that configure real vendor credentials. M0 ships no Feishu
  credentials; coverage uses simulated requests.
- Executor self-registration (`POST /api/enroll`): a new executor submits a
  node fingerprint and capability profile, which lands as a pending
  `enroll_applications` row. An administrator approves or rejects via
  `POST /api/admin/enroll-applications/{id}/decide`; approval creates the
  actor and shows the executor token exactly once. Before approval the
  applicant holds no credential and cannot write the board.
- Admin routes for channel tokens and mappings: issue/list/revoke channel
  tokens, upsert/list/delete channel-user mappings.
- Schema v16 adds `tasks.source_channel`, `tasks.source_user`,
  `channel_tokens`, `channel_users`, and `enroll_applications`. This ships
  task-20260812-012.
### Server: card-pipeline templates, done guardrails, and instance checkpoints
- A reusable multi-card template (`POST /api/card-pipelines`) names a DAG of
  card specs. Instantiating it (`POST /api/card-pipelines/{id}/instantiate`)
  creates one card per node and wires `depends_on` through the existing
  dependency table. The same creator plus `instance_key` is idempotent.
- Acceptance rows may be structured JSON checks (`required_fields`,
  `required_output_field`, `tests_green`). Writing a card to `done` (or
  completing the last pipeline stage) evaluates those checks against the
  submitted `evidence`; a failure is 422 and the card stays open. A pass
  leaves a `guardrail` event on the chain.
- Each instantiation stores a checkpoint. `GET /api/card-pipeline-instances/{id}`
  reports node status and the resume cursor. `POST .../resume` and the
  existing reclaim sweep (`POST /api/tasks/reclaim`) finish a partial
  instantiate from the last created node. Claim and lease stay on M1;
  squad hall cards still go through dispatch_v2 routing.
- Schema v17 adds `card_pipeline_templates` and `card_pipeline_instances`.
  v16 is reserved for task-20260812-012 and is not declared here. This
  ships the scoped slice of task-20260812-007.

### Server: mention trigger, calendar/alert dispatch, squad leader routing
- A `@mention` in a board review comment or a holder note now leaves a
  `mention_trigger` event and a `mention_result` event on the same card.
  An open hall card invites the named executor to claim; a queued card the
  writer holds (or a privileged operator writes) can be reassigned; a card
  already in flight is notified without stealing the baton. Unknown tokens
  are ignored. This extends the Feishu @-mention path onto in-board comments.
- Calendar rows (`POST /api/dispatch/schedules`) and inbound
  `alert` / `callback` events (`POST /api/dispatch/events`) open cards
  through `create_task`. Schedule fires share the existing reclaim sweep
  (`POST /api/tasks/reclaim` and `GET /api/tasks/ready`). Each inbound
  source is idempotent on `(source, idempotency_key)`.
- An open hall card may be addressed to a formation (`squads` +
  `tasks.squad_id`). The leader route reuses `agent-match` over squad
  members and writes one sentence on the chain explaining why that member
  received the baton. The same sweep places unanswered squad cards.
- Schema v15 adds `tasks.squad_id`, `squads`, `squad_members`,
  `dispatch_schedules`, and `dispatch_triggers`. This ships
  task-20260812-010.

### Server: database-atomic sequence allocation
- Task ids and chain-event/attempt sequence numbers no longer depend on an
  in-process lock or a check-then-insert `MAX + 1` read. Allocation is a
  single `INSERT ... ON CONFLICT DO UPDATE` against the new `seq_counters`
  table (schema v14) running inside the caller's own transaction, so
  concurrent processes can never draw the same value, and a rolled-back
  change withdraws its number instead of leaving a gap. The seed side of the
  upsert reads the pre-counter maximum, so imported or pre-upgrade rows keep
  numbering continuity, and the update side stays monotonic if a row was
  inserted out of band.
- `next_task_id`, the per-task chain-event sequence (including the
  orchestration lease events), and the per-task attempt sequence all share
  the one allocator; the module-level `_write_lock` in `server/engine.py` is
  gone. The `(task_id, seq)` unique constraints stay as the backstop.
- Schema v14 adds only the `seq_counters` table; upgrading a v13 database is
  `python -m server.main --data-dir DATA_DIR migrate` as usual, and
  `tests/test_sequence_concurrency.py` proves the allocation under real
  multiprocess fan-out (no duplicates, no gaps). This ships
  task-20260813-003.

### Webui: configurable theme vocabulary, neutral default
- Interface flavour wording (app title, hub and node labels, member roster
  terms, boundary notes) is no longer hard-coded in components: every string
  comes from a `ThemeVocab` record in `webui/src/theme/vocab.ts`, consumed
  through the `useVocab()` hook (`webui/src/theme/ThemeContext.tsx`).
- The default theme is neutral (任务台 / 任务中枢 / 成员 / 节点); the previous
  court-style wording (众卿任务台 / 组织中枢 / 王座) is kept as the `court`
  preset. A sidebar switcher swaps presets at runtime and persists the choice
  in `localStorage` (`retinue.theme`); components without a provider fall
  back to the neutral vocabulary. This ships task-20260813-008.

### Workbench: summary endpoint with incremental polling
- `GET /api/summary` returns the whole first screen in one authenticated,
  read-only call: the five action-queue lanes (waiting for my decision, due
  today, overdue, blocked, lost executors) with counts and the first rows
  each, kanban column counts, the actor roster, pending approvals, and the
  most recent chain events. Response models live in
  `server/routers/summary.py`; access follows the existing login-session
  rule and the endpoint never writes.
- `updated_since` turns the task section into a delta of only the rows that
  changed after the watermark (the response's `generated_at`), so short
  polling stays cheap as the task table grows; aggregates are always
  returned in full because they are bounded. `today` lets the client pin
  the local date the due lanes are computed against, `lane_limit` caps lane
  items, `include_tasks=false` omits the task list entirely, and
  `include_archived` widens the task section for the task center.
- The home page, action queue, and task center now poll the summary
  endpoint and merge changed task rows by id into their cached snapshot,
  replacing the 30-second full-table pulls; the stale-data and retry
  degradation behaviour is unchanged. This ships acceptance item 4 of
  task-20260812-009.
### Server: claim lease, heartbeat, reclaim, retry, and escalate
- Claiming an open card now grants a monotonically increasing lease term.
  Heartbeats renew expiry; liveness is heartbeat-only, so a long run has no
  wall-clock cap. A writer that presents a stale or expired term is refused
  (Raft-style fencing). Defaults match the Multica production set and can be
  overridden with `RETINUE_LEASE_*`: heartbeat 15s, lost 3 minutes, start
  timeout 5 minutes, unclaimed hall 2 hours, retry limit 3.
- Schema v13 adds lease columns on `tasks`, richer `task_attempts` fields
  (term, trigger, session, checkpoint, failure class, workdir), and a
  `workdir_locks` table so two runs cannot share a work directory.
- Lost or never-started leases are swept back to the dispatch hall with a
  chain event. Transient failures retry up to the limit; semantic failures
  and a exhausted retry budget escalate for a human. `tools/retinue_worker.py`
  heartbeats the term and stops writing when fenced.
- Claim responses include a start briefing (similar cards, related sessions,
  bound skills). Deliverable precheck compares submitted checks to acceptance
  and retries or escalates.

### Server: actor-skill bindings, import provenance, claim briefing
- Skills can be bound to an executor independently of the catalog row. Each
  binding has its own enable flag, so a skill can be paused on one actor
  without deleting it or affecting other assignees. Only identities that
  may edit an executor (administrator session) may bind, unbind, or toggle.
  Every change appends a `skill_binding_events` row for that actor.
- Claiming an open card now returns a `skill_briefing` of the claimant's
  enabled bindings. `GET /api/me/skill-briefing` is the same payload for a
  worker that already holds work. `tools/skill_dispatch.py` is the reserved
  generic-worker hook: local cache plus retry on transient failure.
- Runtime import keeps a sanitized source snapshot and the importer. Skills
  whose `source_kind` is repo, runtime, or external carry an explicit
  unreviewed-and-unsandboxed risk notice.
- First operating set: `throne-codex` and `windows-cursor` each receive
  three inventory skills via `POST /api/skills/pilot-bindings` when those
  actors and catalog rows exist.
### Version 0.2.0a2 is the single published spelling
- `pyproject.toml` is now `0.2.0a2`. `server.__version__` (and therefore
  `GET /api/health`) reads that string, falling back to install metadata
  only when `pyproject.toml` is not beside the package. The next git tag
  is `v0.2.0a2`, matching the wheel name `retinue-0.2.0a2-*.whl`.

### Release materials: CycloneDX SBOMs and a license inventory
- `bash scripts/generate_sbom.sh` writes CycloneDX JSON for the
  `retinue[server]` environment and the npm production tree to `dist/`
  (not committed). `python scripts/generate_licenses_inventory.py` turns
  those files into `docs/licenses-inventory.md`.
- The release workflow now writes and checks `SHA256SUMS` for the wheel
  and sdist before the image build, and uploads that file with the
  artifacts.

### Community export: drop dangling panel imports and scan cloud mirrors
- `scripts/export_community.py` now strips quarantined panel imports and
  JSX branches from `App.tsx` and `Operations.tsx` (including the site-console
  switch) before the word rewrite, so the public tree does not import pages
  that were excluded. A factory check runs `tsc -b` and vitest in the
  exported `webui`. The fingerprint scan also refuses internal cloud
  package-mirror hostnames; `scripts/check.sh` enforces the same word list.

### Packaging: wheel, sdist, and a compose hub that is the v0.2 server
- `python -m build` produces a wheel and an sdist from a checkout or from
  the community export. `pip install 'dist/retinue-*.whl[server]'` then
  `python -m server.main --data-dir ./data serve` starts the hub.
  `retinue-server` is the same entry point. `bash scripts/smoke_install.sh
  dist/retinue-*.whl` installs that wheel in a throwaway environment and
  requires `GET /api/health` to report ok.
- The published image and `compose.yaml` start the authenticated server on
  loopback port 9219 (no longer the old read-only panel). The first start
  migrates the data volume and can create the `operator` admin when
  `RETINUE_ADMIN_PASSWORD` is set. The process still runs as `USER retinue`.
- `.github/workflows/release.yml` builds the wheel, sdist, and image on a
  `v*` tag and writes `SHA256SUMS` next to those artifacts.

### Community preview: export isolation rework
- `scripts/export_community.py` drops construction-ledger entries whose
  `file` was excluded, isolates `docs/design/audit-*`, `docs/evidence/`,
  and `scripts/backfill_data_governance.py`, and fails if the export still
  greps as the internal codename or the Chinese realm word. The destination
  gets a fresh git commit so `bash scripts/check.sh` can run there.
- `node/enroll.py` timer-jitter comment no longer names the internal
  deployment.

### Community preview: license, governance, and a clean-export script
- `LICENSE.md` is the official FSL-1.1-ALv2 text (SPDX FSL-1.1-Apache-2.0)
  with JKL Thinking as licensor. `NOTICE.community` states the two-year
  conversion to Apache-2.0 and that the RETINUE name and marks stay with
  JKL Thinking. Internal `LICENSE` / `NOTICE` are unchanged.
- `README.community.md` is the bilingual public introduction and ten-minute
  corridor. `CODE_OF_CONDUCT.md`, `CONTRIBUTING.community.md` (CLA brief
  and signing placeholder), and `SECURITY.community.md` are the public
  governance files. `.github/` has bug and feature issue templates, a
  pull-request template, and a `CODEOWNERS` placeholder (`@jklthinking`).
- `python scripts/export_community.py` writes `dist/community-export/` from
  the current tree using the isolation list in
  `docs/community-preview-v0.1.md`, promotes the community README /
  SECURITY / CONTRIBUTING / NOTICE names, and writes the identifier and
  credential scan (same rules as `scripts/check.sh`) to
  `dist/community-export-scan.txt`, outside the export. The command
  replaces the destination on every run.

### Server: one-way card export into the file shape
- `python scripts/export_cards.py --data-dir ./retinue-data --out <dir>`
  writes every server-mode card as the YAML file shape under `<dir>`, so an
  operator can point it at a repository they control and get `git log` /
  `git blame` for the server's history. The export is strictly one-way (it
  never reads exported files back or writes to the database), deterministic
  (fixed field order, sorted `depends_on`, chain in recorded `seq` order, no
  export-time timestamps — a repeat run against an unchanged database is
  byte-identical), and git-agnostic (it never invokes git; the summary only
  prints a suggested commit message). Session bodies, token hashes, node
  topology, attempts, and review/governance payloads stay in the database.
  This ships adoption item 6 of `docs/design/audit-2026-08.md`.

### Packaging: a node installs on a machine that has almost nothing
- `mcp` moved out of the base dependencies into its own extra. A base or
  `node` install now carries neither an ASGI server nor a crypto library —
  only PyYAML — because no node duty imports mcp; only `core/mcp_server.py`
  and `server/mcp_bridge.py` do.
- The `server` extra keeps the MCP bridge (`server mcp`) by pulling in
  `retinue[mcp]`: the bridge is a server-side surface, and the documented
  server install must not lose it. The `test` extra reaches mcp through
  `retinue[server]`, so the documented contributor install still runs the
  whole suite.
- **Upgrade note (the break):** existing installations are unaffected —
  pip never uninstalls a no-longer-required package, so an environment
  created before this change still has mcp. Only a *fresh* base install
  (`pip install .`) no longer provides `retinue mcp`; there the MCP
  surfaces now refuse with a message naming the extra
  (`pip install 'retinue[mcp]'`) instead of an ImportError traceback, and
  SELF_HOSTING.md plus docs/agent-onboarding.md document it.
- Enrollment can schedule a path-based deployment: `retinue-node enroll
  --package-path <dir>` (or `RETINUE_PACKAGE_PATH`) renders units and
  Windows tasks that carry `PYTHONPATH`, so a node with no pip and no venv
  can run the duties from a copied checkout. Without the flag the rendered
  artifacts are byte-identical to before.

## v0.1.0 — 2026-07-20

First public release: a self-hosted coordination and governance layer for a
heterogeneous AI agent fleet, built around a file-based task-card protocol.

### Protocol (v0.2)
- Task cards as plain YAML on a canonical file bus: six-state machine
  (`queued / doing / handoff / blocked / done / cancelled`), append-only
  event chain, holder-only-writes.
- `priority` enum and `acceptance` list on every card; legacy cards remain
  readable.
- `retinue task lint` validates cards and flags duplicate / sync-conflict
  copies (Syncthing, Git merge artifacts, renamed strays).

### Coordination
- `retinue daemon`: idempotent `on_claim` dispatch — hooks are defined only
  in operator-owned `org.yaml`, never in card content.
- `retinue mcp`: MCP server exposing `task_list`, `my_tasks`, `task_new`,
  `task_update`, `task_receipt` with holder-only-writes enforced at the
  boundary; onboarding docs and a standard skill template included.
- Feishu/Lark IM adapter: automated receipts, one task per thread.

### Observability
- Read-only exporters for Claude Code and Codex session records: daily and
  7-day token accounting with explicit field definitions, atomic metrics
  writes, and honest `cross_runtime_comparable: false` labeling.
- `/overview`: roster with runtime, model, node, recent activity, 15-minute
  online inference, and today's token bars. Board and per-task thread pages.

### Getting started
- `retinue demo --seed 42`: deterministic sample org in one command.
- Reproducible offline static demo (`scripts/build_static_demo.py`).
- README (EN/zh-CN), SELF_HOSTING guide with backup/restore, adapters guide,
  security model (`docs/security.md`), L0–L3 integration ladder,
  Docker/Compose deployment bound to loopback by default.

### Known limits
- File backend designed for roughly 50 agents / 10,000 cards; evolution path
  to SQLite/PostgreSQL documented.
- Panel is read-only and unauthenticated; keep it on loopback or behind an
  authenticating reverse proxy.
