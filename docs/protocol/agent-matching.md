# Explainable agent matching v0.1

The authenticated `GET /api/agent-match` route recommends enabled agent
actors. It is a read model only: calling it does not create a task, claim a
card, change a holder or status, select a node, or start a runtime. Placement
and cross-machine execution remain the orchestrator's responsibility; the
caller chooses whether to act on the returned evidence.

## Purpose and skills

An actor may declare two optional control-plane fields:

- `role`: a short name for the actor's job.
- `goal`: a sentence describing the objective of that job.

There is deliberately no backstory field. Retinue does not assemble or send
prompts, so prompt-writing material does not belong in this registry.

Purpose and skills answer different questions. Role and goal are the actor's
broad declaration of intended responsibility. The existing skills registry is
the authoritative catalogue of concrete capabilities: named, described,
categorized, enabled or disabled, and assigned to owning actors. Declaring a
role or goal never creates a skill, changes a skill owner, or appears in
`matched_skills`. Matching may use both kinds of evidence, with skill relevance
carrying the larger weight.

Actors with no role and no goal remain matchable. For those legacy actors only,
query relevance continues to use the former identity-text fallback so an
unchanged deployment retains its previous capability matching. Empty purpose
adds no points.

## Signals and weights

Scores are deterministic, clamped to 1 through 99, and start at 10.

| Signal | Weight |
| --- | ---: |
| Best assigned enabled skill relevant to the query | 0 to 45 |
| Up to two additional relevant assigned skills | 0 to 10 total |
| Declared role relevant to the query | 0 to 6 |
| Declared goal relevant to the query | 0 to 9 |
| Legacy identity relevance, only when both purpose fields are empty | 0 to 15 |
| Empty query: assigned-skill breadth instead of relevance | 10 plus 5 per skill, capped at 25 |
| Seen within the last 15 minutes | 15 |
| Not seen within the last 15 minutes | 3 |
| Capacity | 15 minus 4 per active card, floored at 0 |
| Delivery history | 2 per completed card, capped at 5 |
| Claimed runtime confirmed by a fresh inventory on the claimed node | 12 |
| Fresh inventory does not confirm the claimed runtime | -8 |
| Node inventory is older than 24 hours | -4 |
| Claimed node has never supplied runtime inventory | -4 |
| Runtime or node needed for the claim is missing | -6 |

A runtime row counts as confirmation only when its node's runtime probe is no
more than 24 hours old and the row says the claimed runtime is available. The
reason includes how the probe found it. A lingering row behind a stale probe is
not confirmation.

Every result explains purpose, skills, recent activity, runtime evidence,
current load, and delivery history in operator-facing reasons. Weak or absent
evidence names the corrective action instead of silently awarding points for
filled-in text.

## Schema evolution

Server schema migration **8** adds `actors.role` and `actors.goal` with empty
defaults. It is additive and preserves baselining; versions 1 through 7 retain
their existing meanings.
