# Security

## Reporting

Report a suspected vulnerability privately. Until the public repository
enables GitHub private vulnerability reporting, use the security contact that
the public repository names in this file. Do not open a public issue. Include
what you did, what happened, and what you expected. If it involves a
credential, do not paste the credential.

This project does not have a paid security team. You will get an
acknowledgement, and if the report is valid you will get a fix and credit
unless you would rather not be named.

报告渠道占位：公开发布仓库启用 GitHub 私密漏洞报告后，以该仓库本文件中的
联系方式为准。在此之前请勿开公开 Issue，也不要在报告里粘贴凭据。

## What this software assumes

Read [`docs/security.md`](docs/security.md) for the full model. The short
version, so you can judge whether a finding is a bug or a documented
boundary:

- **Agents are untrusted.** An agent holds a bearer token bound to one actor
  and can only write to cards it holds. Task text, receipts, transcripts, and
  chat messages are data, never configuration: nothing in them may select a
  command, a path, or an executable.
- **The server expects to be private.** It binds loopback by default, and the
  self-hosting guide warns about publishing it. Exposing it directly to a
  network without an authenticated TLS reverse proxy is outside the model.
- **Two credential kinds, deliberately unequal.** A node token may report a
  heartbeat and an agent-CLI inventory and nothing else. Anything attributed
  to an actor — including session data — needs that actor's token.
- **A node reports metadata, not content.** The inventory carries a runtime
  name, an executable basename, and how it was found. Session sync carries
  only the level the operator configured. Neither sends absolute paths; the
  server rejects one if it arrives.
- **No telemetry, no hosted control plane, no mandatory outbound request.**
  The core, panel, daemon, demo, and exporters work offline.

## Known limits, stated rather than hidden

- **Login throttling is per process.** Counters live in application state, so
  a multi-worker deployment multiplies the effective allowance. Run one
  worker, or put a shared limiter at a trusted gateway. There is no permanent
  lockout; the longest backoff is a minute.
- **The file-mode CLI is an administrative surface.** It writes cards
  directly and does not impose the holder-only rule that agents get through
  the API. Do not hand it to something you would not trust with the data
  directory.
- **File-mode artifact references are not existence-checked.** A card can
  point at a deliverable that is not there.
- **IM publication depends on operator-controlled mapping.** A chat is a
  channel, not an identity: an inbound message becomes a task only if the
  sender is mapped to an actor token that the operator configured. Unknown,
  missing, invalid, and disabled identities create nothing.
