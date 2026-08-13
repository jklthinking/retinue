# Node membership protocol v0.1

The fleet roster is an operator decision, not a telemetry side effect. A node
has one serialized membership state: `admitted` or `retired`. Heartbeats and
runtime inventories may refresh an admitted row, but they never create one or
change membership.

## Credential boundary

Node-attributed reports require an enabled node token whose `node_id` exactly
matches the report. An actor bearer cannot report for any node. An admin web
session also cannot report telemetry: it can deliberately admit or retire a
node and issue its credential, but accepting operator-authored health data
would make the provenance of a node report untrue. The existing cross-node
token refusal remains `403`.

A valid node token for a row that is missing or retired receives an actionable
`403` saying the node is not admitted. Authentication is checked first, so an
unrelated credential cannot use this response to probe fleet membership.

## Admission and token issuance

`POST /api/admin/nodes` is the explicit admission operation. It records the
operator identity and decision time. It can also re-admit a retired row without
deleting the row's earlier telemetry or its last retirement attribution.

An approved roster-proposal card may invoke this same admission operation. The
observer import itself only publishes the card and never constructs a node row
or writes telemetry. Approval therefore remains an explicit membership
decision and the proposal application cannot bypass admission.

Issuing the first token for an unknown node through
`POST /api/admin/node-tokens` admits it in the same transaction. This keeps the
ordinary setup sequence to one successful operator action. The records remain
distinct: admitting a node does not create a credential, rotating or disabling
a token does not change membership, and token issuance for a retired node is
refused until the operator explicitly re-admits it. The local administrative
`issue-node-token` command follows the same first-token admission rule and
records its `--admitted-by` identity.

## Retirement and history

`DELETE /api/admin/nodes/{node_id}` is a soft retirement. It records who made
the decision and when, disables all tokens for that node, and removes the node
from active roster, discovery, matching, orientation, and catalogue views.
The `nodes` row and every `node_runtimes` row remain intact. This preserves the
last heartbeat facts and inventory as audit history while preventing stale
measurements from influencing placement decisions.

## Schema evolution

Server schema migration **9** adds membership state plus admission and
retirement attribution to `nodes`. Every row present in a version-8 database is
grandfathered as `admitted`, with `admitted_by = migration-v9` and
`admitted_at` set to the migration time. No node, heartbeat field, inventory
row, or token is deleted or rewritten. Versions 1 through 8 retain their
existing meanings and unversioned baselining remains contiguous.

Roster proposals and acted-on-behalf-of provenance reuse the existing
versioned task-event payload and unique event key. They add no schema object,
so schema version 10 is intentionally not taken.
