# Native cc-connect observation

`cc_connect` reads native state and Claude JSONL records without modifying either
source. It exports only runtime UUIDs, actual Claude model identifiers, timestamps,
delivery counts and the four token counters. Names, IM identities, chat history,
prompts, content, tool inputs and credentials are never exported. Current and
historical runtime IDs must exist in the native index; unrelated shared-HOME
sessions are excluded. Repeated deliveries are deduplicated across files.

`central` runs on the existing reporting host. It pulls this metadata over fixed,
operator-configured SSH paths. Actor credentials stay on that host, are checked
against `/api/auth/me` and the registered device/model, and are never sent over
SSH. It uses the existing session and metrics APIs and does not launch models,
send chat, acquire leases, create runs or change task status.

An operator-owned configuration uses this shape (neutral example):

```json
{
  "schema_version": 1,
  "node_id": "example-device",
  "interval_seconds": 30,
  "timezone": "Asia/Shanghai",
  "url": "http://127.0.0.1:8000",
  "state_file": "/var/lib/example-observer/state.json",
  "remote": {
    "host": "example-device",
    "python": "/usr/bin/python3",
    "workspace": "/opt/example-observer",
    "sessions_source": "/var/lib/example-native/sessions",
    "transcripts_source": "/var/lib/example-runtime/projects"
  },
  "models": {
    "claude-example-current": {
      "actor_id": "example-device-claude",
      "token_file": "/etc/example-observer/worker.token"
    }
  },
  "observer": {
    "actor_id": "example-device-session-sync",
    "token_file": "/etc/example-observer/observer.token"
  },
  "bindings": []
}
```

The optional existing observer receives only historical model cohorts without a
model-worker mapping. Mapped cohorts use their exact worker; they are never
duplicated under the observer. Historical models never contribute to a different
worker's token totals. Without the observer, unmapped models stay in the private
collector state and health summary; the UI then has limited historical coverage.
The source-generated title labels the actual model, and never uses a chat title.

An optional explicit binding has `native_id`, `model`, and `task_id`. It associates
that exact session cohort through `session.task_id` only. It grants no task-write
authority. UUID/model switching does not automatically carry a binding forward.

Run the read-only preflight before enabling reporting:

```sh
python -m adapters.collectors.central --config /etc/example-observer/config.json --dry-run
python -m adapters.collectors.central --config /etc/example-observer/config.json --once --push
python -m adapters.collectors.central --config /etc/example-observer/config.json --watch --push
```

Dry-run reads no credentials, makes no Retinue API requests and writes no state.
Polling defaults to 30 seconds; operator changes between 5 and 30 seconds and
explicit bindings are read on every loop without a service restart. Keep the
configuration and output directory operator-owned, outside runtime sources, and
retain the state file across restarts. The state lock prevents two processes
sharing that state file. Deployment must select exactly one complete owner for
each actor/runtime daily absolute bucket; do not also run a partial generic usage
reporter for those actors. This lock does not arbitrate separate state files.

For the remote side, install the same `adapters` package and its existing
`core.protocol.task` dependency, including the project's existing PyYAML and
timezone data. The fixed SSH command runs only `adapters.collectors.cc_connect`.
It needs read access to the two selected source directories and no model login,
Retinue token, shell chat integration or provider credentials.

Metrics use `Asia/Shanghai` daily buckets. Cache creation and cache reads are
included once as Claude input cost; output is separate. Legacy server buckets
without a recorded source timezone are not retrospectively rebucketed. Missing
history, unreadable/invalid sources, invalid usage/timestamps, ambiguous models
for one delivery, and incomplete usage prevent all absolute metrics replacement.
A normal intermediate delivery without usage is accepted if a valid final record
with the same identity follows. No evidence means unknown coverage, not zero.
Missing or invalid model attribution also blocks metrics replacement. Only an
explicit `<synthetic>` record with all four counters present, valid and zero is
classified separately as `zero_token_synthetic_records`; it is not a worker
delivery, task progress or an inferred model.

`last_observed_at` and `last_reported_at` describe collection and API receipt;
`last_active_at`/`last_delivery_at` describe actual recorded native activity.
Private state retains current/history/model observations and per-model summaries.
Source counts and message counts are observation, never task progress or success.
There is no native cc-connect task lifecycle API here: specific task progress
still needs an explicit binding and a separately authorized structured report.

Session cursors are reserved durably before POST. Unknown transport outcomes can
repeat the same payload at the same cursor; changed source facts advance the
cursor. Server cursor conflicts fail visibly and never reset or overwrite its
history. HTTP redirects are refused and only fixed error categories are logged.
