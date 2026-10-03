# Live Sessions: ten-minute task-to-tmux walkthrough

This release-candidate walkthrough proves one safe P0 loop on Linux with tmux
and Codex. It uses a deployed checkout, a running Hub, one admitted Node with
its node-token file, one enabled Codex Actor, and an optional task held by that
Actor. Do not use a development worktree as the long-running deployment.

## 1. Discover without taking control

Start Codex in tmux, then list the local inventory. Discovery reads pane and
foreground-process metadata only; it does not read scrollback or send input.

```bash
tmux new-session -s retinue-live
retinue-node sessions --node demo-node-a
```

Copy the exact tmux pane id from the local report. An unbound row may be shown
by the Hub, but it is not control-eligible.

## 2. Bind the verified pane

Run this on the Node after confirming the foreground occupant is Codex:

```bash
retinue-node session-bind --node demo-node-a --pane %12 \
  --actor <agent-id> --session-id live-demo-001 --runtime codex \
  --task <task-id> --input-mode codex-prompt
retinue-node live-cycle --node demo-node-a --url <hub-url> \
  --token-file <node-token-file>
```

The bind command writes tmux user options only. The following cycle lets the
Hub independently verify Actor, task holder, runtime, occupant, and endpoint
generation before it grants control eligibility.

## 3. Observe and talk

Using the target Actor token (or the Web UI), list the verified location and
queue a short correction. Work that needs a deliverable still requires a task
card with acceptance criteria.

```bash
retinue sessions --url <hub-url> --token-file <actor-token-file>
retinue tell live-demo-001 "请先汇报进度，不要开始新的工作。" \
  --url <hub-url> --token-file <actor-token-file> \
  --idempotency-key demo:tell:001
retinue-node live-cycle --node demo-node-a --url <hub-url> \
  --token-file <node-token-file>
```

Inspect the returned envelope with `retinue control-show <control-id>`. A
leased text command is never put back into the queue automatically; uncertain
delivery therefore cannot duplicate terminal input.

## 4. Peek, interrupt, and jump

Peek returns only a bounded Node-redacted tail. Soft interrupt sends one
`Ctrl-C` after a fresh identity/generation check and expires after 30 seconds.
The Web UI requires two clicks for this human action.

```bash
retinue peek live-demo-001 --lines 20 --url <hub-url> \
  --token-file <actor-token-file> --idempotency-key demo:peek:001
retinue interrupt live-demo-001 --url <hub-url> \
  --token-file <actor-token-file> --idempotency-key demo:interrupt:001
retinue jump live-demo-001 --url <hub-url> --token-file <actor-token-file>
```

`jump` resolves the Node and tmux display location; it does not open a shell or
embed a terminal in the browser. Run another live cycle to deliver queued
controls, or explicitly enroll the optional `live` duty after reviewing its
rendered systemd units.

## 5. Finish through the work ledger

Live controls do not complete or reassign tasks. The Agent still reports
progress, handoff, completion, and acceptance through the existing Retinue
task API/MCP tools. This keeps the durable receipt chain separate from the
ephemeral control ledger.

When finished, disable the optional live timer and unbind the pane if that
runtime identity is no longer active. Never reuse a prior live-session id for
a different Actor or runtime.
