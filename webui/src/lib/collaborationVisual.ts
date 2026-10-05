import { t } from "../i18n";
import type { ActorInfo, TaskSummary } from "../types";
import type { CollaborationEvent, CollaborationRun, CollaborationSnapshot, CollaborationRelationship, CollaborationModule } from "./collaboration";

export type CollaborationTask = TaskSummary & { parent_task_id?: string | null; module?: string | null };
export type HistoryEvent = CollaborationEvent;
/** Optional extensions preserve compatibility with servers predating the graph. */
export interface VisualSnapshot extends CollaborationSnapshot {
  tasks: (CollaborationTask & { parent_task_id: string | null })[];
  events: HistoryEvent[];
  related_tasks?: CollaborationTask[];
  relationships?: CollaborationRelationship[];
  modules?: CollaborationModule[];
}
export interface VisualEdge {
  id: string;
  kind: "delegation" | CollaborationRelationship["kind"];
  from: string;
  to: string;
  label: string;
  note: string;
  at?: string | null;
}
export interface PositionedTask {
  task: CollaborationTask;
  external: boolean;
  x: number;
  y: number;
}
export const EDGE_LABEL = { delegation: "委派", dependency: "依赖", handoff: "交棒", review_return: "退回" };
export const GRAPH_NODE_WIDTH = 220;
export const GRAPH_NODE_HEIGHT = 112;

export function visualEdges(snapshot: VisualSnapshot, nameOf: (id: string) => string): VisualEdge[] {
  const edges: VisualEdge[] = snapshot.delegations.map((item) => ({
    id: item.id, kind: "delegation", from: item.parent_task_id, to: item.child_task_id,
    label: t("委派"), note: `${nameOf(item.delegated_by)} → ${nameOf(item.delegated_to)}`, at: item.created_at,
  }));
  for (const item of snapshot.relationships ?? []) {
    if (!(item.kind in EDGE_LABEL)) continue;
    edges.push({ id: item.id, kind: item.kind, from: item.from_task_id, to: item.to_task_id,
      label: t(EDGE_LABEL[item.kind]),
      note: [item.from_actor || item.to_actor ? `${nameOf(item.from_actor || "")} → ${nameOf(item.to_actor || "")}` : "", item.note].filter(Boolean).join(" · "),
      at: item.at,
    });
  }
  // Explicit dependency IDs are safe for older snapshots; they never establish delegation.
  const available = new Set([...snapshot.tasks, ...(snapshot.related_tasks ?? [])].map((task) => task.id));
  for (const task of snapshot.tasks) for (const dependency of task.depends_on ?? []) {
    if (available.has(dependency) && !edges.some((edge) => edge.kind === "dependency" && edge.from === dependency && edge.to === task.id)) {
      edges.push({ id: `dependency:${dependency}:${task.id}`, kind: "dependency", from: dependency, to: task.id, label: t("依赖"), note: t("任务卡显式依赖") });
    }
  }
  return edges;
}

/** Bounded ranks handle branches and cyclic dependency data without inventing ancestry. */
export function layoutTasks(snapshot: VisualSnapshot, edges: VisualEdge[]): { nodes: PositionedTask[]; width: number; height: number } {
  const primaryIds = new Set(snapshot.tasks.map((task) => task.id));
  const tasks = [...snapshot.tasks, ...(snapshot.related_tasks ?? []).filter((task) => !primaryIds.has(task.id))];
  const ranks = new Map<string, number>([[snapshot.root_task_id, 0]]);
  const structural = edges.filter((edge) => edge.kind === "delegation");
  const queue = [snapshot.root_task_id];
  while (queue.length) {
    const parent = queue.shift()!;
    for (const edge of structural.filter((item) => item.from === parent)) {
      if (ranks.has(edge.to)) continue;
      ranks.set(edge.to, (ranks.get(parent) ?? 0) + 1);
      queue.push(edge.to);
    }
  }
  for (const task of tasks) if (!ranks.has(task.id)) ranks.set(task.id, primaryIds.has(task.id) ? 1 : 0);
  const groups = new Map<number, CollaborationTask[]>();
  for (const task of tasks) {
    const rank = ranks.get(task.id)!;
    const group = groups.get(rank) ?? [];
    group.push(task);
    groups.set(rank, group);
  }
  const height = Math.max(200, ...[...groups.values()].map((group) => group.length * 140 + 64));
  const nodes = tasks.map((task) => {
    const rank = ranks.get(task.id)!;
    const group = groups.get(rank)!;
    return { task, external: !primaryIds.has(task.id), x: 24 + rank * 296,
      y: 52 + group.indexOf(task) * 140 + (height - 64 - group.length * 140) / 2 };
  });
  return { nodes, width: Math.max(360, 48 + (Math.max(0, ...ranks.values()) + 1) * 296), height };
}

export function visibleModules(snapshot: VisualSnapshot): CollaborationModule[] {
  if (snapshot.modules?.length) return snapshot.modules;
  const groups = new Map<string, CollaborationModule>();
  const group = (name: string | null | undefined): CollaborationModule => {
    const key = name?.trim() || "";
    if (!groups.has(key)) groups.set(key, {
      id: `fallback-module:${key}`, name: key || t("未标注模块"), source: key ? "explicit" : "unassigned",
      task_ids: [], run_ids: [], completed: [],
    });
    return groups.get(key)!;
  };
  const taskModules = new Map(snapshot.tasks.map((task) => [task.id, task.module?.trim() || ""]));
  const instrumented = new Set(snapshot.runs.map((run) => run.task_id));
  for (const task of snapshot.tasks) {
    const module = taskModules.get(task.id);
    if (module || !instrumented.has(task.id)) group(module).task_ids.push(task.id);
  }
  for (const run of snapshot.runs) {
    const module = run.module?.trim() || taskModules.get(run.task_id);
    const item = group(module);
    if (!item.task_ids.includes(run.task_id)) item.task_ids.push(run.task_id);
    item.run_ids.push(run.id);
    for (const completed of run.progress_report?.completed ?? []) {
      const target = group(completed.module?.trim() || module);
      if (!target.task_ids.includes(run.task_id)) target.task_ids.push(run.task_id);
      if (!target.run_ids.includes(run.id)) target.run_ids.push(run.id);
      target.completed.push({ task_id: run.task_id, run_id: run.id, actor_id: run.actor_id,
        summary: completed.summary, refs: completed.refs, revision: completed.revision,
        at: run.progress_reported_at ?? null, verification: "unverified" });
    }
  }
  return [...groups.values()];
}

export function historicalWorkerLabel(actorId: string, actors: ActorInfo[]): string {
  return (actors.find((actor) => actor.id === actorId)?.display_name || actorId || t("责任人未知")) + t(" · 历史设备/模型未记录");
}

export function millis(value: string | null | undefined): number | null {
  if (!value) return null;
  const time = new Date(value).getTime();
  return Number.isFinite(time) ? time : null;
}

export function runBounds(run: CollaborationRun, generatedAt: string): { start: number; end: number; prepared: boolean } | null {
  const prepared = run.execution_state === "prepared";
  const start = millis(prepared ? run.prepared_at || run.updated_at : run.started_at);
  const active = ["running", "waiting"].includes(run.status) && run.lease_current !== false && run.lease_live !== false;
  const end = millis(run.ended_at || (prepared ? run.prepared_at || run.updated_at : active ? generatedAt : run.updated_at));
  if (start === null || end === null) return null;
  // Clock skew is disclosed instead of being turned into a negative duration.
  return { start, end: Math.max(start, end), prepared };
}
