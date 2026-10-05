import { t } from "../i18n";
import { fetchJSON } from "@/lib/api";

export type KingdomNodeId = "node-a" | "node-b";
export type KingdomView = "overview" | "agents" | "tasks" | "skills" | "knowledge" | "infrastructure";
export type AlertSeverity = "critical" | "warning" | "info";

export interface KingdomGateway {
  unit: string;
  active: string;
  sub: string;
  healthy: boolean;
  current_process_healthy?: boolean | null;
  observed_at?: string | null;
  snapshot_stale?: boolean;
  status_basis?: string;
  restarts: number;
  memory_bytes?: number;
}

export interface KingdomAgent {
  id: string;
  node: KingdomNodeId;
  profile: string;
  display_name: string;
  role: string;
  kind: string;
  model: string;
  provider: string;
  gateway: KingdomGateway;
  observed_at?: string | null;
  snapshot_stale?: boolean;
  sessions: number;
  messages: number;
  skill_visible: number;
  skill_local: number;
}

export interface KingdomSkill {
  name: string;
  description: string;
  category: string;
  enabled: boolean;
  nodes: KingdomNodeId[];
  visible_to: string[];
  owned_by: string[];
  sources: Array<"agent" | "shared" | "bundled" | string>;
}

export interface KingdomCron {
  id: string;
  agent_id: string;
  profile: string;
  name: string;
  enabled: boolean;
  schedule: string;
  last_status: string;
  last_run_at?: string | number | null;
  next_run_at?: string | number | null;
}

export interface KingdomTask {
  id: string;
  node: KingdomNodeId;
  title: string;
  agent_id?: string | null;
  assignee: string;
  status: string;
  priority: number;
  created_at?: number | string | null;
  started_at?: number | string | null;
  completed_at?: number | string | null;
  failures: number;
  goal_mode: boolean;
}

export interface KingdomSession {
  id: string;
  agent_id: string;
  source: string;
  model: string;
  started_at?: number | string | null;
  ended_at?: number | string | null;
  message_count: number;
  archived: boolean;
}

export interface KingdomVault {
  available: boolean;
  node?: KingdomNodeId;
  kind?: string;
  path_label?: string;
  active_notes?: number;
  active_files?: number;
  active_size_bytes?: number;
  versions?: number;
  version_files?: number;
  conflicts?: number;
  git_dirty?: number;
  git_remotes?: number;
  sync_warnings_24h?: number;
  sync_failures_24h?: number;
  latest_mtime?: string | null;
  folders?: Array<{ name: string; notes: number }>;
  filebrowser_url?: string | null;
  peer_count?: number;
  connected_peers?: number;
  sync_peers?: Array<{
    id: "windows" | "node-a" | "node-b" | "ll" | string;
    label: string;
    role: string;
    tailscale_ip: string;
    path_label: string;
    warehouse_path?: string | null;
    folder_member?: boolean;
    connected: boolean;
    paused: boolean;
    client_version: string;
    observed_at?: string | null;
  }>;
  admission?: {
    available: boolean;
    status?: string;
    policy_version?: string;
    generated_at?: string;
    scanned_documents?: number;
    promoted?: number;
    unchanged?: number;
    duplicates?: number;
    rejected?: number;
    withdrawn?: number;
    pending?: number;
    redactions?: { emails?: number; phones?: number };
    catalog_projects?: number;
    output_audit_hits?: number;
    output_secret_hits_remaining?: number;
  };
  insight_pipeline?: {
    notes: number;
    by_status: Record<string, number>;
    scored: number;
    unscored: number;
    complete_structure: number;
    merge_candidates: number;
    overdue: number;
    template_available: boolean;
    dashboard_available: boolean;
  };
}

export interface KingdomKnowledgeSource {
  node: KingdomNodeId;
  shared_notes: number;
  shared_wiki_notes: number;
  shared_memory_notes: number;
  raw_mirror_notes: number;
}

export interface KingdomNode {
  id: KingdomNodeId;
  label: string;
  hostname: string;
  platform: string;
  uptime_seconds: number;
  load: number[];
  freshness_seconds: number;
  stale: boolean;
  generated_at: string;
  snapshot_at?: string | null;
  disk: { total: number; used: number; free: number; percent: number };
  memory: { total: number; available: number; used: number; swap_total: number; swap_free: number };
  totals: {
    agents: number;
    active_gateways: number;
    skills: number;
    crons: number;
    sessions: number;
    messages: number;
    tasks: number;
  };
  vault: KingdomVault;
  knowledge: Omit<KingdomKnowledgeSource, "node">;
}

export interface KingdomService {
  id: string;
  label: string;
  kind: "gateway" | "service" | "container" | string;
  node: KingdomNodeId;
  active: string;
  sub: string;
  unit_file_state?: string;
  healthy: boolean;
  restarts: number;
  memory_bytes?: number;
}

export interface KingdomAlert {
  id: string;
  node: KingdomNodeId;
  severity: AlertSeverity;
  title: string;
  detail: string;
}

export interface KingdomTotals {
  nodes: number;
  agents: number;
  active_gateways: number;
  skills: number;
  crons: number;
  sessions: number;
  messages: number;
  tasks: number;
  vault_notes: number;
  alerts: number;
}

export interface KingdomOverview {
  schema_version: number;
  generated_at: string;
  nodes: KingdomNode[];
  agents: KingdomAgent[];
  skills: KingdomSkill[];
  sessions: { total: number; messages: number; recent: KingdomSession[] };
  crons: KingdomCron[];
  tasks: { total: number; by_status: Record<string, number>; items: KingdomTask[] };
  vault: KingdomVault;
  vault_replicas: KingdomVault[];
  knowledge_sources: KingdomKnowledgeSource[];
  services: KingdomService[];
  alerts: KingdomAlert[];
  totals: KingdomTotals;
}

export interface KingdomKnowledgeOverview {
  schema_version: number;
  generated_at: string;
  vault: KingdomVault;
  knowledge_sources: KingdomKnowledgeSource[];
}

export interface KingdomAction {
  node: KingdomNodeId;
  action:
    | "cron_pause"
    | "cron_resume"
    | "cron_run"
    | "gateway_restart"
    | "service_restart"
    | "task_create";
  confirm: true;
  profile?: string;
  target_id?: string;
  title?: string;
  body?: string;
  assignee?: string;
  priority?: number;
  goal?: boolean;
}

export interface KingdomActionResult {
  ok: boolean;
  node: KingdomNodeId;
  action?: string;
  output?: string;
  error?: string;
  duration_ms?: number;
}

export interface KingdomAuditItem {
  node: KingdomNodeId;
  timestamp: string;
  ok: boolean;
  action?: string;
  profile?: string;
  target_id?: string;
  title?: string;
  returncode?: number;
  duration_ms?: number;
  error?: string;
}

export interface KingdomAuditGap {
  node: KingdomNodeId;
  reason: string;
  returncode?: number;
}

export interface VaultConflictItem {
  path: string;
  name: string;
  original_name: string;
  original_path?: string | null;
  device?: string | null;
  conflict_at?: string | null;
  size: number;
  mtime?: string | null;
  archived: boolean;
  editable: boolean;
}

export interface VaultConflictDetail {
  conflict: VaultConflictItem & { content?: string };
  original: { path: string; content: string } | null;
  candidates: string[];
  diff: string[];
}

export interface VaultConflictResolve {
  path: string;
  action: "keep_current" | "keep_conflict" | "save_edit";
  target?: string;
  content?: string;
  confirm: true;
}

export const kingdomApi = {
  getConflicts: () =>
    fetchJSON<{ available: boolean; root?: string; items: VaultConflictItem[] }>("/api/kingdom/conflicts", undefined, { timeoutMs: 12_000 }),
  getConflictDetail: (path: string) =>
    fetchJSON<VaultConflictDetail>(`/api/kingdom/conflicts/detail?path=${encodeURIComponent(path)}`, undefined, { timeoutMs: 12_000 }),
  resolveConflict: (body: VaultConflictResolve) =>
    fetchJSON<{ ok: boolean; trashed?: string; backup?: string }>("/api/kingdom/conflicts/resolve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  getOverview: (view: KingdomView) =>
    fetchJSON<KingdomOverview>(`/api/kingdom/overview?view=${encodeURIComponent(view)}`, undefined, { timeoutMs: 12_000 }),
  getKnowledge: () => fetchJSON<KingdomKnowledgeOverview>("/api/kingdom/knowledge", undefined, { timeoutMs: 12_000 }),
  runAction: (body: KingdomAction) =>
    fetchJSON<KingdomActionResult>("/api/kingdom/actions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  refresh: async () => {
    const response = await fetchJSON<{ ok: boolean; results: KingdomActionResult[] }>("/api/kingdom/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm: true }),
    });
    if (!response.ok) {
      const failed = response.results.filter((item) => !item.ok);
      throw new Error(failed.map((item) => `${item.node}: ${item.error || t("刷新失败")}`).join("；"));
    }
    return response;
  },
  getAudit: () => fetchJSON<{ items: KingdomAuditItem[]; gaps?: KingdomAuditGap[] }>("/api/kingdom/audit"),
};
