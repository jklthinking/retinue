export type Status = "queued" | "doing" | "handoff" | "blocked" | "done" | "cancelled";
export type Priority = "urgent" | "high" | "medium" | "low" | "none";

export interface ChainEvent {
  who: string;
  did: string;
  at: string;
  from_status: Status | null;
  to_status: Status | null;
  from_holder: string | null;
  to_holder: string | null;
  payload: {
    acted_on_behalf_of?: {
      authorising_identity: string;
      performing_agent: string;
    };
  } & Record<string, unknown>;
}

export interface RosterProposalItem {
  key: string;
  kind: "actor" | "task" | "node" | "skill" | "knowledge_source";
  identity: string;
  action: "create";
  fields: Record<string, string | number | boolean | string[]>;
}

export interface RosterProposal {
  version: number;
  idempotence_key: string;
  items: RosterProposalItem[];
}

export interface TaskAttempt {
  id: string;
  seq: number;
  reporter: {
    kind: "actor" | "operator" | "node";
    id: string;
    duty: string | null;
  };
  started_at: string;
  ended_at: string;
  outcome: "succeeded" | "failed" | "cancelled";
  reason: string | null;
  exit_status: number | null;
  reported_at: string;
}

export interface TaskSummary {
  id: string;
  title: string;
  created_by: string;
  dept: string | null;
  priority: Priority;
  status: Status;
  holder: string;
  blocked_reason: string | null;
  next: string | null;
  blocked_by: TaskRelation[];
  blocks: TaskRelation[];
  depends_on: string[];
  due_at: string | null;
  ready: boolean;
  acceptance: string[];
  refs: string[];
  progress: number;
  open_dispatch: boolean;
  squad_id?: string | null;
  pipeline: PipelineStage[] | null;
  pipeline_stage: number;
  archived: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export interface TaskRelation {
  id: string;
  title: string;
  status: Status;
  kind: "blocks";
}

export interface Task extends TaskSummary {
  chain: ChainEvent[];
  attempts: TaskAttempt[];
  proposal: RosterProposal | null;
}

export interface ActorInfo {
  id: string;
  kind: "human" | "agent";
  display_name: string;
  role: string;
  goal: string;
  runtime: string;
  model: string;
  node: string;
  disabled: boolean;
  last_seen_at: string | null;
  online: boolean;
}

export interface AgentMatchInfo {
  id: string;
  display_name: string;
  role: string;
  goal: string;
  runtime: string;
  model: string;
  node: string;
  online: boolean;
  score: number;
  matched_skills: string[];
  reasons: string[];
  active_tasks: number;
  completed_tasks: number;
}

export interface RuntimeDiscoveryInfo {
  runtime: string;
  label: string;
  source: string;
  local_detected: boolean;
  path_hint: string | null;
  last_changed_at: string | null;
  session_count: number;
  last_activity_at: string | null;
  last_probe_at: string | null;
  nodes: RuntimeNodeInfo[];
  agent_ids: string[];
  registered: boolean;
}

export interface RuntimeNodeInfo {
  id: string;
  label: string;
  detected_at: string | null;
}

export interface DiscoveryAttention {
  actor_id: string;
  display_name: string;
  runtime: string;
  node: string;
  missing: string[];
  online: boolean;
}

export interface AgentDiscoveryInfo {
  scanned_at: string;
  scope: string;
  privacy: string;
  runtimes: RuntimeDiscoveryInfo[];
  attention: DiscoveryAttention[];
  actions: string[];
}
export interface Me {
  kind: string;
  name: string;
  role: "admin" | "member" | "viewer" | "agent";
  actor_id: string | null;
  display_name: string;
  site_console?: boolean;
  mode?: string;
  site_label?: string;
  readonly?: boolean;
}

export interface MetricsActor {
  actor_id: string;
  days: Record<string, { input: number; output: number }>;
  input: number;
  output: number;
}

export interface MetricsSummary {
  start: string;
  days: number;
  actors: MetricsActor[];
}

export type GateKind = "auto" | "review" | "queen";

export interface PipelineStage {
  name: string;
  holder: string;
  gate: GateKind;
}

export interface ApprovalInfo {
  id: number;
  task_id: string;
  task_title: string | null;
  stage_index: number;
  stage_name: string | null;
  status: "pending" | "approved" | "rejected";
  requested_by: string;
  decided_by: string | null;
  decision_note: string;
  created_at: string | null;
  decided_at: string | null;
}

export interface PipelineTemplateInfo {
  id: number;
  name: string;
  stages: PipelineStage[];
}

/** One row of the server-computed recent chain events in /api/summary. */
export interface SummaryEvent {
  who: string;
  did: string;
  at: string;
  from_status: Status | null;
  to_status: Status | null;
  task_id: string;
  task_title: string;
}

export interface LaneSummary<T> {
  count: number;
  items: T[];
}

export interface LostExecutorRow {
  task: TaskSummary;
  actor: ActorInfo;
}

export interface SummaryLanes {
  decisions: LaneSummary<ApprovalInfo>;
  due_today: LaneSummary<TaskSummary>;
  overdue: LaneSummary<TaskSummary>;
  blocked: LaneSummary<TaskSummary>;
  lost_executors: LaneSummary<LostExecutorRow>;
}

/** GET /api/summary response: first-screen aggregates plus (optionally
 * incremental) task rows. */
export interface SummaryInfo {
  generated_at: string;
  today: string;
  partial: boolean;
  task_counts: Record<string, number>;
  lanes: SummaryLanes;
  approvals: ApprovalInfo[];
  actors: ActorInfo[];
  tasks: TaskSummary[] | null;
  recent_events: SummaryEvent[];
}

export const GATE_LABEL: Record<GateKind, string> = {
  auto: "自动接力",
  review: "审阅",
  queen: "人工审批",
};

/** One QC comment still waiting for a reply (inbox reviews lane). */
export interface InboxReviewItem {
  task_id: string;
  task_title: string;
  review_id: string;
  author: string;
  body: string;
  created_at: string;
}

/** One stale in-flight card plus why it counts as stale (inbox stale lane). */
export interface InboxStaleRow {
  task: TaskSummary;
  reasons: string[];
}

export interface InboxLanes {
  decisions: LaneSummary<ApprovalInfo>;
  reviews: LaneSummary<InboxReviewItem>;
  blocked: LaneSummary<TaskSummary>;
  stale: LaneSummary<InboxStaleRow>;
}

export interface InboxDigestInfo {
  date: string;
  enabled: boolean;
  registered: number;
  owners: number;
  delivered: number;
}

/** GET /api/inbox response: four attention lanes plus the digest nudge. */
export interface InboxInfo {
  generated_at: string;
  today: string;
  lanes: InboxLanes;
  digest: InboxDigestInfo;
}

export interface SkillInfo {
  id: number;
  name: string;
  description: string;
  category: string;
  enabled: boolean;
  owners: string[];
  source: string;
  updated_at: string | null;
}

export interface ServiceState {
  unit: string;
  label?: string | null;
  active: string;
  sub?: string;
  restarts?: number;
  healthy: boolean;
}

export type NodeRuntimeState = "never_probed" | "probed_empty" | "probed_found";

export type NodeRuntimeDataState = "unknown" | "none" | "present";

export type WatermarkLevel = "ok" | "warn" | "high" | "critical" | "unknown";

export interface NodeWatermark {
  disk: WatermarkLevel;
  load: WatermarkLevel;
}

export interface NodeRuntimeEntry {
  node_id: string;
  runtime: string;
  command: string;
  available: boolean;
  source: string;
  path_hint: string | null;
  data_changed_at: string | null;
  data_state: NodeRuntimeDataState;
  detected_at: string | null;
  updated_at: string | null;
}

export interface NodeInfo {
  id: string;
  label: string;
  hostname: string;
  platform: string;
  uptime_seconds: number;
  load: number[];
  disk: { total?: number; used?: number; free?: number; percent?: number };
  memory: { total?: number; available?: number };
  services: ServiceState[];
  runtimes_probed_at: string | null;
  data_dirs_probed_at: string | null;
  runtime_state: NodeRuntimeState;
  runtimes: NodeRuntimeEntry[];
  updated_at: string | null;
  watermark?: NodeWatermark;
}

export interface KnowledgeInfo {
  id: number;
  name: string;
  kind: string;
  location: string;
  docs: number;
  size_bytes: number;
  notes: string;
  updated_at: string | null;
}

export interface ThroughputDay {
  date: string;
  done: number;
  receipts: number;
}

export interface Throughput {
  start: string;
  days: ThroughputDay[];
  done_by_actor: { actor_id: string; done: number }[];
}

export type SessionPrivacy = "metadata" | "summary" | "full";

export interface SessionMessage {
  role: "user" | "assistant" | "system";
  text: string;
  at: string | null;
}

export interface RuntimeSessionInfo {
  id: number;
  actor_id: string;
  actor_name: string;
  runtime: string;
  node: string;
  title: string;
  summary: string;
  privacy: SessionPrivacy;
  cursor: number;
  message_count: number;
  messages: SessionMessage[];
  task_id: string | null;
  task_title: string | null;
  resume_capable: boolean;
  started_at: string | null;
  updated_at: string | null;
  synced_at: string | null;
}

export interface StatusInfo {
  version: string;
  task_counts: Record<string, number>;
  actors: number;
  online_actors: number;
  skills: number;
  nodes: number;
  knowledge_sources: number;
}

export function fmtBytes(value: number): string {
  let size = value;
  const units = ["B", "KB", "MB", "GB", "TB"];
  let index = 0;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  return `${size.toFixed(index > 1 ? 1 : 0)} ${units[index]}`;
}

export function fmtUptime(seconds: number): string {
  const days = Math.floor(seconds / 86400);
  if (days > 0) return `${days} 天`;
  const hours = Math.floor(seconds / 3600);
  return hours > 0 ? `${hours} 小时` : `${Math.floor(seconds / 60)} 分钟`;
}

/** Local calendar day in YYYY-MM-DD form, matching the server-side due_at. */
export function localTodayISO(): string {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

export const TRANSITIONS: Record<Status, Status[]> = {
  queued: ["doing", "cancelled"],
  doing: ["handoff", "blocked", "done", "cancelled"],
  handoff: ["doing", "cancelled"],
  blocked: ["doing", "cancelled"],
  done: [],
  cancelled: [],
};

export const STATUS_LABEL: Record<Status, string> = {
  queued: "待办",
  doing: "进行中",
  handoff: "移交中",
  blocked: "受阻",
  done: "已完成",
  cancelled: "已取消",
};

export const PRIORITY_LABEL: Record<Priority, string> = {
  urgent: "紧急",
  high: "高",
  medium: "中",
  low: "低",
  none: "—",
};

/* Private todo hub (个人事务): proposals await owner confirmation before they
 * become items; items may be promoted into shared board cards (task_id). */
export interface TodoProposal {
  id: string;
  owner_user_id: number;
  proposed_by: string;
  title: string;
  notes: string;
  due_at: string | null;
  remind_at: string | null;
  source_channel: string | null;
  source_backlink: string | null;
  status: string;
  todo_item_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface TodoItem {
  id: string;
  owner_user_id: number;
  title: string;
  notes: string;
  status: string;
  due_at: string | null;
  remind_at: string | null;
  proposal_id: string | null;
  source_channel: string | null;
  source_backlink: string | null;
  task_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface TodoWaitingItem extends TodoItem {
  task_holder: string | null;
  task_status: string;
}

/** Aggregate payload of GET /api/todos/home. */
export interface TodoHome {
  pending_proposals: TodoProposal[];
  due_today: TodoItem[];
  overdue: TodoItem[];
  waiting_on_others: TodoWaitingItem[];
}
