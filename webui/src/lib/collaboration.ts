import { t, getLanguage } from "../i18n";
import type { Status, TaskAttempt, TaskSummary } from "../types";

export interface CollaborationDelegation {
  id: string;
  parent_task_id: string;
  child_task_id: string;
  parent_run_id: string | null;
  delegated_by: string;
  delegated_to: string;
  title: string;
  instruction: string;
  acceptance: string[];
  created_at: string;
  module?: string | null;
}

export type IdentitySource = "reported" | "registry" | "unknown";

export interface CompletedClaim {
  summary: string;
  refs: string[];
  revision?: string | null;
  module?: string | null;
}

export interface ProgressReport {
  completed: CompletedClaim[];
  remaining: string[];
  next_action: string | null;
  next_owner: string | null;
}

export interface RunReporter {
  kind: string;
  id: string;
}

export interface CollaborationRun {
  id: string;
  task_id: string;
  parent_task_id: string | null;
  actor_id: string;
  model: string | null;
  module?: string | null;
  node?: string | null;
  runtime?: string | null;
  node_source?: IdentitySource;
  runtime_source?: IdentitySource;
  model_source?: IdentitySource;
  identity_recorded_at?: string | null;
  identity_complete?: boolean;
  execution_state?: "prepared" | "started";
  prepared_at?: string | null;
  execution_authorized_at?: string | null;
  reported_by?: RunReporter | null;
  last_reported_by?: RunReporter | null;
  progress_report?: ProgressReport | null;
  progress_reported_at?: string | null;
  last_report_at?: string | null;
  heartbeat_at?: string | null;
  freshness?: { state: "unknown" | "fresh" | "stale"; age_seconds: number | null; stale_after_seconds: number };
  status: "queued" | "running" | "waiting" | "succeeded" | "failed" | "cancelled";
  title: string;
  delegation_id: string | null;
  parent_run_id: string | null;
  started_at: string | null;
  ended_at: string | null;
  updated_at: string;
  progress: { completed: number; total: number; unit: string } | null;
  waiting: {
    kind: "input" | "review" | "human" | "external" | "dependency";
    owner: string;
    reason: string;
    since: string;
  } | null;
  latest_note: string;
  refs: string[];
  attempt_id: string | null;
  lease_term: number;
  lease_current?: boolean;
  lease_live?: boolean;
  session_ref?: string | null;
}

export interface CollaborationEvent {
  id: number;
  task_id: string;
  seq: number;
  type: string;
  who: string;
  did: string;
  at: string;
  payload: Record<string, unknown>;
  from_status?: Status | null;
  to_status?: Status | null;
  from_holder?: string | null;
  to_holder?: string | null;
}

export interface CollaborationRelationship {
  id: string;
  kind: "dependency" | "handoff" | "review_return";
  from_task_id: string;
  to_task_id: string;
  from_actor?: string | null;
  to_actor?: string | null;
  at?: string | null;
  note?: string | null;
  event_id?: number | null;
  source?: string;
}

export interface CollaborationModule {
  id: string;
  name: string;
  source: "explicit" | "unassigned";
  task_ids: string[];
  run_ids: string[];
  completed: {
    task_id: string; run_id: string; actor_id: string; summary: string; refs: string[];
    revision?: string | null; at: string | null; verification: "unverified";
  }[];
}

export interface CollaborationSnapshot {
  task_id: string;
  root_task_id: string;
  generated_at: string;
  cursor: { event_id: number; attempt_id: number };
  tasks: (TaskSummary & { parent_task_id: string | null; module?: string | null })[];
  related_tasks?: (TaskSummary & { module?: string | null })[];
  relationships?: CollaborationRelationship[];
  modules?: CollaborationModule[];
  delegations: CollaborationDelegation[];
  runs: CollaborationRun[];
  events: CollaborationEvent[];
  attempts: (TaskAttempt & { task_id: string })[];
  recovery: {
    task_id: string;
    available: boolean;
    reason: string;
    action: "prepare_retry";
    endpoint: string | null;
    plan: unknown;
  }[];
  coverage: { instrumented_tasks: number; total_tasks: number };
  can_delegate?: boolean;
  delegation_reason?: string;
  delegation_lease_term?: number | null;
  delegation_targets?: string[];
  can_manage_delegation_policy?: boolean;
  delegation_policy?: { allowed_actor_ids: string[]; max_depth: number; max_children: number } | null;
}

export const RUN_STATUS: Record<CollaborationRun["status"], string> = {
  queued: "待接单",
  running: "执行中",
  waiting: "等待中",
  succeeded: "执行成功",
  failed: "执行失败",
  cancelled: "已取消",
};

export const WAIT_KIND: Record<NonNullable<CollaborationRun["waiting"]>["kind"], string> = {
  input: "等待资料",
  review: "等待审校",
  human: "等待人工",
  external: "等待外部服务",
  dependency: "等待上游",
};

export function countedProgress(run: CollaborationRun): string {
  const progress = run.progress;
  if (!progress || !Number.isFinite(progress.completed) || !Number.isFinite(progress.total)
    || progress.total <= 0 || progress.completed < 0 || progress.completed > progress.total) {
    return t("尚未上报可计数进度");
  }
  return t("已完成 {v0}/{v1} {v2}", { v0: progress.completed, v1: progress.total, v2: progress.unit }).trim();
}

export function collaborationTime(value: string | null | undefined): string {
  if (!value) return t("尚未记录");
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return value;
  return new Intl.DateTimeFormat(getLanguage(), {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  }).format(date);
}

export function runIdentity(run: CollaborationRun): string {
  return (run.node || t("设备未知")) + " · " + (run.model || t("模型未知"));
}

export function missingIdentity(run: CollaborationRun): string[] {
  return [
    !run.node ? t("设备") : "",
    !run.model ? t("模型") : "",
    !run.runtime ? t("运行端") : "",
  ].filter(Boolean);
}

export function runStatus(run: CollaborationRun): string {
  return (run.lease_current === false || run.lease_live === false) && ["running", "waiting"].includes(run.status)
    ? t("状态待核实") : run.execution_state === "prepared" && ["running", "waiting", "queued"].includes(run.status)
      ? t("待启动") : t(RUN_STATUS[run.status]);
}

/** Completed execution reports remain historical evidence, not overdue work. */
export function needsProgressUpdate(run: CollaborationRun): boolean {
  return ["running", "waiting"].includes(run.status) && run.freshness?.state === "stale";
}

export function identitySource(source: IdentitySource | undefined): string {
  if (source === "reported") return t("本次上报");
  if (source === "registry") return t("当次登记");
  return t("来源未记录");
}

export function reporterLabel(reporter: RunReporter | null | undefined): string {
  if (!reporter) return t("记录者未上报");
  const role = ({ actor: "Agent", agent: "Agent", user: t("操作员"), operator: t("操作员"), node: t("设备") } as Record<string, string>)[reporter.kind] || reporter.kind;
  return role + " " + reporter.id;
}
export interface TaskContextSnapshot {
  version: 1;
  task_id: string;
  root_task_id: string;
  generated_at: string;
  revision: { event_id: number; attempt_id: number; task_updated_at: string; content_hash: string };
  task: TaskSummary;
  collaboration: Pick<CollaborationSnapshot, "tasks" | "delegations" | "coverage">;
  runs: (CollaborationRun & { session_link?: { id: number; access: "permitted" } | null })[];
  active_runs: CollaborationRun[];
  confirmed_progress: {
    task_id: string; status: "done"; event_id: number; at: string; who: string;
    confirmation: "recorded_task_state"; acceptance_verified: false;
  }[];
  unverified_claims: {
    task_id: string; run_id: string; actor_id: string; completed: CompletedClaim[];
    progress: CollaborationRun["progress"]; reported_at: string | null;
    verification: "unverified"; freshness: CollaborationRun["freshness"];
  }[];
  evidence: {
    task_id: string; run_id: string | null; summary: string; refs: string[];
    revision: string | null; source: "executor_report" | "task_record"; verification: "unverified";
  }[];
  next: {
    task_id: string; run_id: string; action: string | null; owner: string | null;
    remaining: string[]; waiting: CollaborationRun["waiting"]; source: "executor_report"; grants_authority: false;
  }[];
  privacy: { contains_private_conversations: boolean; contains_session_summaries: boolean; session_links: string };
}
