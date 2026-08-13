import { vi } from "vitest";
import type { ActorInfo, Me, SummaryInfo, Task } from "../types";

export const ME: Me = {
  kind: "user",
  name: "operator",
  role: "member",
  actor_id: "agent-one",
  display_name: "Operator",
  readonly: false,
};

export const AGENT_ONE: ActorInfo = {
  id: "agent-one",
  kind: "agent",
  display_name: "Agent One",
  role: "",
  goal: "",
  runtime: "kimi",
  model: "",
  node: "",
  disabled: false,
  last_seen_at: "2026-08-12T10:00:00",
  online: true,
};

export const AGENT_TWO: ActorInfo = {
  ...AGENT_ONE,
  id: "agent-two",
  display_name: "Agent Two",
  online: false,
  last_seen_at: "2026-08-11T08:00:00",
};

export function makeTask(overrides: Partial<Task> = {}): Task {
  return {
    id: "task-20260812-001",
    title: "样例任务",
    created_by: "agent-one",
    dept: "工程",
    priority: "medium",
    status: "queued",
    holder: "agent-one",
    blocked_reason: null,
    next: null,
    blocked_by: [],
    blocks: [],
    depends_on: [],
    due_at: null,
    ready: true,
    acceptance: [],
    refs: [],
    progress: 0,
    open_dispatch: false,
    squad_id: null,
    pipeline: null,
    pipeline_stage: 0,
    archived: false,
    created_at: "2026-08-12T09:00:00",
    updated_at: "2026-08-12T09:00:00",
    chain: [],
    attempts: [],
    proposal: null,
    ...overrides,
  };
}

type RouteHandler = (
  init?: RequestInit,
  url?: string
) => { status?: number; body: unknown };

/** A full-shape /api/summary payload; tests override the sections they need. */
export function makeSummary(overrides: Partial<SummaryInfo> = {}): SummaryInfo {
  return {
    generated_at: "2026-08-12T10:00:00+00:00",
    today: "2026-08-12",
    partial: false,
    task_counts: {},
    lanes: {
      decisions: { count: 0, items: [] },
      due_today: { count: 0, items: [] },
      overdue: { count: 0, items: [] },
      blocked: { count: 0, items: [] },
      lost_executors: { count: 0, items: [] },
    },
    approvals: [],
    actors: [],
    tasks: [],
    recent_events: [],
    ...overrides,
  };
}

/** Stub window.fetch with per-URL handlers; unmatched URLs fail loudly. */
export function mockFetch(routes: Record<string, RouteHandler>) {
  const calls: { url: string; init?: RequestInit }[] = [];
  const stub = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({ url, init });
    for (const [prefix, handler] of Object.entries(routes)) {
      if (url.startsWith(prefix)) {
        const { status = 200, body } = handler(init, url);
        return new Response(JSON.stringify(body), {
          status,
          headers: { "Content-Type": "application/json" },
        });
      }
    }
    return new Response(JSON.stringify({ detail: `unmocked ${url}` }), {
      status: 500,
      headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", stub);
  return { stub, calls };
}
