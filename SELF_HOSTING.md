# Self-hosting Retinue

Retinue needs Python 3.10+ and an operator-controlled data directory. Runtime
CLIs are optional and must be installed separately on nodes that invoke them.
The panel has no authentication; its safe default is loopback only.

## Native install and first start

From a trusted source checkout:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install .
retinue init ./retinue-data --org personal-lab
retinue panel ./retinue-data
```

The base install is deliberately small: the standard library plus PyYAML.
If agents will coordinate over MCP (`retinue mcp`), install that surface
explicitly — it is an extra because it pulls in an ASGI server, a crypto
library, and an HTTP client that nothing else needs:

```bash
pip install '.[mcp]'
```

Without the extra, the MCP commands refuse with a message naming it rather
than an ImportError traceback.

Open <http://127.0.0.1:8787/>. Edit `retinue-data/org.yaml` to register nodes,
agents, and operator-approved `on_claim` hooks. Never place credentials or
commands sourced from task cards in that file. Run one watcher per configured
node when dispatch is needed:

```bash
retinue daemon ./retinue-data --node workstation
```

## Node-only install (managed nodes)

A managed node reports heartbeats, runtime inventories, and the
privacy-scoped session index, but never runs the server. Install with the
`node` extra so the web framework, ORM, ASGI server, and crypto library are
not required — the node duties run on the standard library plus PyYAML, the
only base dependency:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install '.[node]'
```

A node whose account cannot even build a virtual environment (no pip, no
`ensurepip`, a managed interpreter) can still run the duties: copy the
package directories (`core`, `adapters`, `node`) onto the node's disk and
point `PYTHONPATH` at them. The only third-party requirement is PyYAML,
which managed images usually already carry:

```bash
PYTHONPATH=<package-dir> python3 -m node.cli whoami --node <node-id>
```

Enroll such a deployment with `--package-path` (or
`RETINUE_PACKAGE_PATH`) so the rendered units and scheduled tasks carry the
import path themselves:

```bash
PYTHONPATH=<package-dir> python3 -m node.cli enroll --target linux-user \
  --package-path <package-dir> --node <node-id> \
  --url http://127.0.0.1:9219 --token-file <token-file> \
  --duties heartbeat,runtimes
```

A managed node authenticates with up to **two credential kinds**, and they
are not interchangeable:

- **Node token** — a heartbeat-only credential bound to one infrastructure
  node. Issue it on the server and copy the file to the node:

  ```bash
  python -m server.main issue-node-token --node <node-id> --output <token-file>
  ```

  The heartbeat and runtime-inventory duties authenticate with this token;
  the session-sync route does not accept it.
- **Actor API token** — belongs to an actor (agent), because session sync is
  attributed to an actor and carries conversation-derived data. Issue it on
  the server (printed exactly once) and save it to its own file on the node,
  separate from the node token:

  ```bash
  python -m server.main issue-token --actor <agent-id>
  ```

Then configure the node through the environment or equivalent arguments:

```bash
export RETINUE_SERVER_URL=http://127.0.0.1:9219
export RETINUE_NODE_ID=<node-id>
export RETINUE_NODE_TOKEN_FILE=<token-file>          # node token
export RETINUE_ACTOR_TOKEN_FILE=<actor-token-file>   # actor API token
retinue-node whoami          # prints what would be sent; sends nothing
retinue-node heartbeat       # infrastructure health heartbeat
retinue-node runtimes        # agent CLI inventory (basenames only)
retinue-node sync-sessions --runtime codex --source <sessions-dir> --actor <agent-id>
```

The inventory reports a runtime id, an executable basename, availability,
and how it was found (`path`, `well-known`, or `pin`) — never an absolute
path — and the session sync never writes into a runtime's own transcript
directory.

## Enroll a node schedule

`retinue-node enroll` installs the schedule for the duties the operator
selects with `--duties` (comma-separated; default all three): heartbeat
and runtime inventory **hourly**, the session index **daily**. Selecting a
subset is explicit — on a node whose session collection is handled
centrally, enroll with `--duties heartbeat,runtimes` and no session
configuration at all. The install target is always chosen explicitly with
`--target` — it is never guessed from the host. Render mode is the default
and prints exactly what would be installed, byte for byte, without writing
or activating anything; `--install` is a separate, explicit action.
Enrolling is idempotent: unit files and scheduled tasks have fixed names
and are replaced in place, so re-running after an upgrade updates the
schedule instead of duplicating it.

Each duty carries the credential kind it actually authenticates with: the
heartbeat and inventory units use the node token (`--token-file`), and the
session unit uses the actor API token, supplied separately with
`--actor-token-file` (or `RETINUE_ACTOR_TOKEN_FILE`). Asking for the session
duty without a readable actor token file is refused before anything is
written — enrollment never installs a unit that is certain to fail. A node
enrolled with only `--duties heartbeat,runtimes` needs exactly one secret:
the node token.

Review first on any target (renders, changes nothing):

```bash
retinue-node enroll --target <target> --node <node-id> \
  --url http://127.0.0.1:9219 --token-file <token-file> \
  --actor-token-file <actor-token-file> \
  --runtime codex --source <sessions-dir> --actor <agent-id>
```

Missing configuration, an unreadable or empty token file of either kind, or
an unsupported target is refused with a message naming exactly what is
missing — before anything is written, never as a partial install.

**Linux, system-wide** (operator has root; units land in
`/etc/systemd/system`):

```bash
sudo .venv/bin/retinue-node enroll --target linux-system --install \
  --node <node-id> --url http://127.0.0.1:9219 --token-file <token-file> \
  --actor-token-file <actor-token-file> \
  --runtime codex --source <sessions-dir> --actor <agent-id>
```

**Linux, per-user** (account has no privilege escalation; units land in
`~/.config/systemd/user`, no root needed):

```bash
retinue-node enroll --target linux-user --install \
  --node <node-id> --url http://127.0.0.1:9219 --token-file <token-file> \
  --actor-token-file <actor-token-file> \
  --runtime codex --source <sessions-dir> --actor <agent-id>
loginctl enable-linger "$USER"   # optional: timers also run while logged out
```

**Windows** (Python and git present; nothing else assumed). The enrollment
creates one scheduled task per selected duty via `schtasks` with `/F`
(replace in place):

```bat
retinue-node enroll --target windows --install ^
  --node <node-id> --url http://127.0.0.1:9219 --token-file <token-file> ^
  --actor-token-file <actor-token-file> ^
  --runtime codex --source <sessions-dir> --actor <agent-id>
```

**Node duties only** (session collection handled elsewhere; no actor
credential needed):

```bash
retinue-node enroll --target linux-user --install \
  --node <node-id> --url http://127.0.0.1:9219 --token-file <token-file> \
  --duties heartbeat,runtimes
```

Verify after install:

```bash
systemctl list-timers 'retinue-node-*'        # or: systemctl --user list-timers 'retinue-node-*'
schtasks /Query /TN "Retinue Node Heartbeat"  # Windows
```

Then confirm the node is actually reporting: its row on the server (panel
nodes view or `GET /api/nodes`) shows a fresh heartbeat timestamp and the
runtime inventory under `GET /api/nodes/<node-id>/runtimes` within the hour,
and — if the session duty was enrolled — the session index updates daily.

## Per-node executable pins

A runtime id does not name the same executable on every machine. A pin is
the operator's per-node answer: *on this machine, use this exact
executable*. The pin belongs to the node, not to any shared profile — the
same runtime can be pinned to a different path on every machine that needs
one.

Two real situations call for a pin:

- **An interpreter whose venv support is missing.** The node's default
  Python cannot create a virtual environment and the account cannot install
  the support, but a second interpreter on the same machine works. Pin
  `interpreter` to that second interpreter and enrolment renders it into the
  schedule instead of the default derivation — no more guessing search
  order in a shell script.
- **Executables absent from a scheduled unit's PATH.** A workstation plainly
  has agent history, yet the hourly inventory reports nothing because the
  CLI lives outside the PATH a systemd timer or scheduled task sees and
  outside the conventional directories the probe searches. Pin the runtime
  and the inventory reports it with `source: "pin"` — the basename and how
  it was found, never where.

Pins live in a JSON file on the node itself — never in a tracked file, a
server payload, or a response. The location is
`~/.config/retinue/runtime-pins.json` (POSIX) or
`~/AppData/Roaming/retinue/runtime-pins.json` (Windows), overridable with
`RETINUE_RUNTIME_PINS_FILE`:

```json
{
  "interpreter": "/abs/path/to/python3.11",
  "runtimes": {
    "codex": "/abs/path/to/codex"
  }
}
```

Every value must be an absolute path; a malformed file or a relative path is
refused with a message naming the file. A pinned interpreter that does not
exist is refused at enrolment, before anything is written; a pinned runtime
whose file is missing is ignored (the normal search still runs) and is
explained by the diagnostic below. Pinned binaries are never executed —
detection is existence checks only.

When a runtime is not found where you expected it, ask where the search
looked:

```bash
retinue-node runtimes --explain
```

This prints, locally, the pin file consulted, each runtime's pin status, the
PATH result, and every conventional directory searched — then pushes
nothing and needs no credential. Normal output and the inventory payload
never contain these locations.

## Upgrade

For the database-backed server, schema migration is an explicit deployment
step. Opening an existing database never upgrades it: code that needs a newer
schema refuses to start and reports the version it found, the version it
requires, and the migration command. A database for which schema inspection
finds no tables is the only exception; first-run setup creates that fresh
database directly at the current schema version. If inspection finds even one
table, the database is existing state and is never treated as fresh.

Upgrade the server in this order:

1. Stop the server and every scheduled or administrative process that can
   write its database. Keep all writers stopped through the migration.
2. Take and verify a backup of the server data directory as described below.
3. In the source checkout, install the reviewed release with
   `pip install --upgrade .`.
4. With the writers still stopped, run the explicit, idempotent migration:

   ```bash
   python -m server.main --data-dir ./retinue-server-data migrate
   ```

   The command reports the schema version before and after the operation. Run
   it again if desired; a current database reports the same version twice and
   makes no schema changes.
5. Start the server, inspect its board and overview, then re-enable its
   scheduled jobs. This is the complete writer sequence: **stop, migrate,
   start**.

For a file-backed panel deployment, stop the panel and every daemon, take and
verify the portable-state backup below, install the reviewed release, run
`retinue task lint ./retinue-data/tasks`, then start the panel and daemons.

Protocol readers remain compatible with v0.1 task cards. Review release notes
before crossing later protocol versions; do not skip a documented migration.

## Backup

Choose the backup that matches the deployment shape. A file-backed deployment
and a database-backed server do not have interchangeable backups.

### File-backed portable state

Retinue's portable state is the entire data directory: `org.yaml`, `tasks/`,
`metrics/`, and `nodes/`. Stop writers, then copy it as one unit:

```bash
cp -a ./retinue-data ./retinue-data.backup-YYYYMMDD
retinue task lint ./retinue-data.backup-YYYYMMDD/tasks
```

Store the backup on different media and protect it like task content. Runtime
transcripts and credentials are deliberately outside this directory and need
their own backup policy. Exported metrics snapshots are included; their
read-only transcript sources are not.

The same directory can be privately versioned for auditability:

```bash
git -C ./retinue-data init
git -C ./retinue-data add org.yaml tasks metrics nodes
git -C ./retinue-data commit -m "backup Retinue state"
```

Keep that repository private. Inspect changes before every commit and never
add adapter credentials, environment files, or runtime transcripts.

### Database-backed server state

For a database-backed server, the off-machine backup deliberately contains
exactly one file: `retinue.db`. The scheduled backup must take a consistent
SQLite snapshot, transfer that snapshot to another machine, and compare the
destination's SHA-256 digest with the source digest. Copying a live database
file is not a substitute for a consistent snapshot.

This single-file backup deliberately does **not** include:

- the directory containing plaintext agent and node token files;
- environment files, secret-manager contents, or adapter and IM credentials;
- runtime transcripts or other runtime-owned session sources; or
- host service definitions, reverse-proxy configuration, and TLS material.

The database retains irreversible credential hashes, not the corresponding
plaintext tokens. Sending the plaintext-token directory or other deployment
secrets to the off-machine backup would enlarge their exposure surface and
turn the backup destination into another credential store. Keep those items
under separate secret and configuration recovery policies. Consequently, a
server restore requires an operator to reissue and redistribute every agent
and node credential, and to restore deployment configuration and adapter
secrets from their separate sources.

## Restore and rehearse it

First identify the deployment shape:

- If the source of truth is `org.yaml`, `tasks/`, `metrics/`, and `nodes/`, use
  **File-backed restore**.
- If the source of truth is the server's single `retinue.db`, use
  **Database-backed server restore**. Do not apply the file-backed lint and
  panel procedure to a database backup.

### File-backed restore

Restore only while the panel and daemons are stopped:

```bash
mv ./retinue-data ./retinue-data.damaged
cp -a ./retinue-data.backup-YYYYMMDD ./retinue-data
retinue task lint ./retinue-data/tasks
retinue panel ./retinue-data --port 8878
```

Confirm card counts and latest receipts on the board, inspect both runtime rows
on the overview, then stop the rehearsal panel and restart normal services.
Keep the damaged directory until acceptance is complete so rollback remains
possible.

### Database-backed server restore

Use the following procedure for the single-file off-machine server backup:

1. Stop the server and every scheduled or administrative process that can
   write its database. Keep all writers stopped until migration and credential
   issuance are finished.
2. Retrieve the verified off-machine `retinue.db` snapshot. Check its digest
   against the SHA-256 value recorded when the destination copy was accepted:

   ```bash
   sha256sum <off-machine-backup>/retinue.db
   ```

3. If the damaged server data directory still exists, move it aside. Create a
   clean data directory, copy in only the snapshot, and verify that the copied
   file has the same digest:

   ```bash
   mv <server-data-dir> <server-data-dir>.damaged
   mkdir -p <server-data-dir>
   cp <off-machine-backup>/retinue.db <server-data-dir>/retinue.db
   sha256sum <server-data-dir>/retinue.db
   ```

   Keep the damaged directory until restore acceptance is complete. On a
   replacement host where it no longer exists, omit only the `mv` command.
4. With all other writers still stopped, run the explicit, idempotent schema
   migration for the reviewed release:

   ```bash
   python -m server.main --data-dir <server-data-dir> migrate
   ```

5. Reissue credentials from the restored roster. Issue a new actor API token
   for every agent and every dedicated actor-bound integration credential;
   each value is printed once:

   ```bash
   python -m server.main --data-dir <server-data-dir> issue-token --actor <agent-id>
   ```

   Issue a new node token for every admitted node:

   ```bash
   python -m server.main --data-dir <server-data-dir> issue-node-token \
     --node <node-id> --output <new-node-token-file>
   ```

   Deliver each new value through the approved secret channel, replace the
   corresponding agent, node, MCP, session-sync, and sender-mapping token files
   or secret values, and preserve per-agent and per-node separation. The old
   plaintext tokens cannot be derived from the hashes in the restored
   database.
6. Restore the deliberately excluded environment, adapter, reverse-proxy, TLS,
   and service configuration from the operator's separate configuration and
   secret recovery sources. Do not copy them from an untrusted machine or add
   them to the database backup.
7. Start the reviewed server deployment. For a foreground start, the existing
   server command is:

   ```bash
   python -m server.main --data-dir <server-data-dir> serve
   ```

8. Confirm the board's card, event, actor, and node counts and inspect the
   latest receipts. Then make one authenticated request with every reissued
   credential kind before re-enabling scheduled jobs and agents.

Restoring the database is not by itself a successful recovery. The board can
start and display all of its records while **every agent receives `401`**:
the database contains only irreversible credential hashes, and the plaintext
token directory is not in the backup. Credential reissuance and distribution
in step 5 are therefore mandatory, not post-restore cleanup.

### Rehearse the documented procedures

Run each drill against isolated destinations without touching production. For
file-backed state, copy the backup to `./retinue-restore-drill`, lint it, open
its panel on port 8878, and compare its latest task receipts with the source
snapshot. For a database-backed server, follow the numbered database restore
procedure above using `<restore-drill-data-dir>`, including digest comparison,
migration, fresh drill credential issuance and distribution, and an
authenticated request after the isolated server starts.

A drill must follow this document step by step; a hand-written recovery that
merely proves the archive or database can be opened does not validate the
document. The previous drill tested the backup artifact by restoring it
manually, so the mismatch between the documented file-backed procedure and the
actual single-database deployment was not discovered. A backup is not accepted
until its documented procedure completes and both restored data and restored
authentication are verified.

## Docker Compose

The included image runs as an unprivileged user and stores state in the named
`retinue-data` volume. Initialize once, then start the panel:

```bash
docker compose build
docker compose run --rm retinue init /data --org personal-lab
docker compose up -d
docker compose logs -f retinue
```

The container listens on all interfaces *inside its private network* so Docker
can forward traffic, but Compose publishes it only as
`127.0.0.1:8787:8787` on the host.

Back up the named volume while the service is stopped:

```bash
docker compose stop retinue
docker run --rm -v retinue_retinue-data:/data:ro -v "$PWD":/backup \
  alpine tar -C /data -czf /backup/retinue-data.tgz .
docker compose start retinue
```

The actual volume name can differ when Compose uses another project name;
verify it with `docker volume ls` before backup or restore. Test archives by
restoring into a new named volume and running `retinue task lint /data/tasks`
in a one-off container.

## Agent card discovery (optional, off by default)

The server can publish an A2A-style Agent Card at
`/.well-known/agent-card.json` so conforming clients can discover what this
deployment hosts. It is **off unless an operator turns it on**:

```bash
export RETINUE_AGENT_CARD=1   # truthy: 1, true, yes, on; anything else is off
```

The card is discovery-only: it lists the enabled skills of the registry and
no actor, node, token, address, or path material, and this deployment serves
no A2A task endpoint. See `docs/protocol/a2a-discovery.md` for the identity
decision, the per-field safety argument, and the vocabulary mapping.

## Port and reverse-proxy warning

**Do not change the host mapping to `0.0.0.0:8787:8787` or use
`retinue panel --host 0.0.0.0` on an untrusted network.** The panel is read-only
but unauthenticated and may reveal task and runtime metadata. Wider exposure is
an operator decision: put an authenticated TLS reverse proxy in front, apply a
network allowlist, keep the Retinue process on a private interface, and test
that direct port access is blocked.

Retinue itself has no telemetry. Optional IM adapters and runtime CLIs have
their own network and credential behavior; audit those components separately.
