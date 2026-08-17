# Reminder delivery (M0)

Personal todo reminders register idempotent slots in `reminder_deliveries`
(schema v18). This batch adds the delivery scanner and two channel plugins.

## Deploy configuration

Copy [`docs/examples/reminders.yaml`](examples/reminders.yaml) to
`<data-dir>/reminders.yaml` on the host. Real webhook URLs and any shared
secrets belong only in that data-directory file — never in the git tree.

## Scanner cadence

`server/reminders.deliver_due_reminders` is shaped like the dispatch calendar
fire helper: a due query plus an idempotent side effect. It is invoked from the
existing reclaim / ready sweep paths (`POST /api/tasks/reclaim`,
`GET /api/tasks/ready`) so operators keep one polling rhythm. `dispatch_v2.py`
is not modified.

## Channels

- `in_app` — appends owner-visible events on the private todo event chain
  (`reminder_delivered` / `reminder_channel_ok`).
- `webhook` — `POST` JSON to the configured URL. Default body is
  `{title, scheduled_for}`; set `detail_level: detail` for notes and ids.
  Timeouts and retries are capped by `timeout_seconds` and `max_attempts`.

Failed channel attempts leave `reminder_delivery_failed` events. After
`max_attempts` failures the slot becomes `abandoned` with a
`reminder_abandoned` audit event. Successful slots become `delivered` and are
never resent.
