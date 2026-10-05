# Retinue version journal — English sharing draft

**English** · [中文小红书草稿](xiaohongshu.md) · [English screenshots](../releases/2026-10-03-english-edition.md)

Editable publishing material. This file does not send or publish a social post. Every screenshot uses synthetic data.

## Suggested titles

1. Making multi-agent collaboration visible, one task at a time
2. Who asked which AI to do what? Building a collaboration dashboard
3. Retinue update: task graphs, worker lanes, and module contributions

## Post

I am building Retinue, a self-hosted tool for coordinating AI workers across different devices and runtimes.

Several models can be online and produce plenty of chat, while it is still difficult to answer a basic question: who delegated this task, how far has it progressed, and who is it waiting for?

This update puts that process into the dashboard:

- A relationship graph for each task, with explicit delegation, dependencies, handoffs, and review returns.
- Device, runtime, and model identities alongside execution lanes.
- Clickable nodes for instructions, reported work, remaining items, waits, artifacts, and next steps.
- Functional-module contributions showing what each part of a task has reported.
- Task context for a new session to continue with criteria and evidence versions.
- Operations views that explain usage sources, coverage, and freshness. Missing reports remain unknown.

The original home task flow, dispatch coordination, and session flow remain. Duplicate operations entrances now share one implementation. The dashboard, PRD, and screenshot gallery are available in English as well as Chinese.

There is still more to connect. Observing a native session does not automatically produce structured task progress, and a worker's completion claim still needs independent acceptance. The next step is more explicit task binding and real progress receipts, followed by immutable GitHub evidence checks.

Retinue's own code is MIT licensed. The eight screenshots follow one synthetic product-launch task, with deterministic demo stage times. They illustrate the interface and protocol, rather than a real-model acceptance run.

Repository: [jklthinking/retinue](https://github.com/jklthinking/retinue)

Try the [English read-only demo](https://jklthinking.github.io/retinue/demo-en/?lang=en) or read the [English PRD](../PRD.en.md).

#MultiAgent #AITools #OpenSource #ProductDevelopment #IndieDev #Visualization

## Image order and captions

| Order | Image | Caption |
|---|---|---|
| 1 | `01-home.jpg` | Start with the overview: original home task flow retained — synthetic demo |
| 2 | `02-task-collaboration.jpg` | One task, multiple workers: explicit delegation and dependencies — synthetic demo |
| 3 | `03-operations.jpg` | Usage with source, coverage, and report time — synthetic demo |
| 4 | `04-data-quality.jpg` | Inspect structure, freshness, and retained history separately — synthetic demo |
| 5 | `05-worker-lanes.jpg` | Which device/model participated in each stage — synthetic demo times |
| 6 | `06-module-contributions.jpg` | Launch coordination, feature checks, and copy contributions — synthetic demo |
| 7 | `07-branch-detail.jpg` | Open a branch: instructions, progress, waits, artifacts, and next steps — synthetic demo |
| 8 | `08-home-dispatch.jpg` | Dispatch coordination and session flow still available — synthetic demo |

Images 2, 5, 6, and 7 are views of the same fixed task. Keep the synthetic-demo caption when sharing. Normal cropping is fine; do not change the records or numbers shown.

## Short version

> Retinue now shows collaboration within each task: relationship graphs for delegation and returns, device/model lanes for participation, and module contributions for reported work. Usage retains source, coverage, and freshness. The original home visualizations remain, and the interface now switches between Chinese and English. Retinue code is MIT licensed; all preview images are synthetic demonstrations.
