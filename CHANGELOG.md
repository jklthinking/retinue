# Changelog

## Unreleased

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
