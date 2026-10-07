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
