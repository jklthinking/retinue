# Node model quota

The Home dashboard shows reported **used percentages**, reset times, report age,
stale data and the last successful reading after a failed query. Subscription
probes support Claude, Codex, Grok, Cursor and Kimi. Moonshot API accounts show a
balance rather than a subscription percentage. Additional administrative-provider
names are extension placeholders, not implemented probes.

Enable collection separately on each node. It defaults to off:

```bash
retinue-node quota --enable claude codex
retinue-node quota --status
retinue-node quota --node sample-node --dry-run
retinue-node quota --node sample-node --url http://127.0.0.1:9219 --token-file /path/to/node-token
```

Enrollment also accepts `--quota-consent all|none|claude,codex,...`. Merely
rendering an enrollment schedule does not enable collection. Use `--disable`
to withdraw consent. Unconsented providers neither read credentials nor start
their CLI. Configuration lives in `~/.config/retinue/quota.json` (Windows:
`~/AppData/Roaming/retinue/quota.json`); `RETINUE_QUOTA_CONFIG` overrides it.

Credentials stay on the node and are used only to authenticate fixed provider
usage endpoints. The collector sends no inference prompt and does not log
tokens, cookies, email or account IDs. Kimi runs its built-in `/usage` in a
temporary empty directory; its normal CLI startup may refresh credentials. A
timeout or unavailable terminal falls back to an unexpired credential's usage
API. One provider's failure does not stop the others.

The optional user-level systemd service/timer templates in `deploy/systemd`
are not installed automatically. Configure the node environment and CLI PATH
before installing them. Collection/reporting is separate from dashboard refresh.

## Server and display semantics

- Schema 26 adds quota reports and snapshots. Follow the explicit stop, verified
  backup, migrate, start procedure in [Self hosting](../SELF_HOSTING.md#upgrade).
  Opening an old database does not migrate it automatically.
- `POST /api/nodes/quota` accepts only an admitted node's matching node token.
  Bodies are bounded to 256 KiB and use a strict schema. Raw credentials are
  rejected; validation errors do not echo submitted values.
- Authenticated `GET /api/quota` groups accounts and includes report age,
  windows, balance and labelled last-successful data. `?compact=1` provides a
  conservative per-provider view; `/api/quota/history` offers 1–90 UTC days.
- Reports older than 26 hours are stale. Storage prunes reports older than
  90 days during ingestion. A reset time passing does **not** set usage to zero:
  the UI says it is awaiting the next report.
- Reset clock times use UTC+08:00, labelled explicitly in the English UI.
  Relative reset/fetch times refresh locally every minute. The dashboard fetches
  on its normal refresh schedule and supports manual refresh.
- Account fingerprints support grouping but are not displayed. Multiple accounts
  use generic ordinals. No data is shown as a local opt-in instruction; unknown
  providers and unconfigured or unconsented placeholders are hidden.
- English and Chinese demo sites include only frozen synthetic examples. Opening
  a demo never probes local credentials or contacts providers.

See [the detailed Chinese contract](node-quota.md) for payloads, limits,
deduplication and provider configuration.

## Manual refresh (schema 27)

The Home **Refresh** button asks nodes to query vendors again; reading the old
`GET /api/quota` snapshot alone never counts as success. Only administrators can
start it by default. Set `RETINUE_QUOTA_REFRESH_MEMBERS=1` to allow members or
`RETINUE_QUOTA_REFRESH=0` to disable the feature. Re-enrollment without a new
`--quota-consent` selection preserves existing consent and proxy settings.

`POST /api/quota/refresh` accepts an idempotent `request_key` and optional
allowlisted `providers` and `nodes`. Node credentials claim only their own work
with `POST /api/nodes/quota/refresh/claim` and `{node}` (204 when idle). Fresh
reports carry the claimed `refresh_request_id`. Authenticated readers can inspect
`GET /api/quota/refresh/{batch_id}`; it includes no credentials or account data.
No remote field can become a command, argument, path, or vendor URL.

The queue expires after 120 seconds, claims after 180 seconds, and local
collection after 150 seconds. The UI distinguishes pending, querying, complete,
partial failure and timeout, and retains the previous values when no fresh
successful receipt exists. Same-scope active requests are reused; a different
scope conflicts. Completed work has a 120-second cooldown and each node is
limited to 48 requests per UTC day. Batch membership stays immutable.

Run `retinue-node quota-poll` with the same local node environment as the daily
collector. The new systemd user samples in `deploy/systemd/` poll every 30 seconds
and share a collection lock with the daily timer. After reviewing the node's
consent and PATH, operators copy both quota-poll samples into the user unit
directory and run `systemctl --user daemon-reload` followed by
`systemctl --user enable --now retinue-node-quota-poll.timer`.
They are opt-in deployment samples and are never auto-enabled by installation.
Existing heartbeat, runtimes, session and live-control schedules are unchanged.
Windows may schedule the same command locally; typical latency is 30–120 seconds.

Stop all writers and make a consistent SQLite backup before explicit migration.
Schema 27 adds only request and batch tables, with 90-day retention. Prefer a
feature-flag rollback plus disabling the poll timer. Restoring an older binary
also requires restoring the pre-upgrade database, losing later writes, because
old binaries reject newer schemas.

The initial UI summarizes each batch rather than expanding node countdowns.
Node timestamps allow 120 seconds of clock skew; completion uses Hub receipt
time. Windows process timeouts mark the whole batch failed and discard partial
results; this fallback has not been exercised on a physical Windows node.
