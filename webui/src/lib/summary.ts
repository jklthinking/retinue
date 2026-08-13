import { useCallback, useEffect, useRef, useState } from "react";
import { api, readErrorMessage } from "../api";
import type { SummaryInfo, TaskSummary } from "../types";

/** Client for the first-screen summary endpoint with incremental polling.
 *
 * The first fetch is a full snapshot; every later fetch carries the last
 * response's `generated_at` as `updated_since`, so the server only returns
 * task rows that changed. Aggregates (lanes, counts, actors, approvals,
 * recent events) always come back in full — they are bounded and cheap —
 * while the unbounded task list is merged by id on top of the cached
 * snapshot. Errors keep the previous data visible (stale), matching the
 * existing degradation contract.
 */

export interface SummaryQuery {
  since?: string | null;
  today?: string;
  includeTasks?: boolean;
  includeArchived?: boolean;
}

export function summaryPath({
  since,
  today,
  includeTasks = true,
  includeArchived = false,
}: SummaryQuery = {}): string {
  const params = new URLSearchParams();
  if (since) params.set("updated_since", since);
  if (today) params.set("today", today);
  if (!includeTasks) params.set("include_tasks", "false");
  if (includeArchived) params.set("include_archived", "true");
  const query = params.toString();
  return `/api/summary${query ? `?${query}` : ""}`;
}

export function fetchSummary(query: SummaryQuery = {}): Promise<SummaryInfo> {
  return api.get<SummaryInfo>(summaryPath(query));
}

/** Upsert changed rows into the cached list, keeping the server's ordering
 * (created_at desc, id desc). Tasks are never deleted — archival arrives as
 * a changed row with `archived: true` — so no tombstones are needed. */
export function mergeTasks(
  current: TaskSummary[],
  changed: TaskSummary[]
): TaskSummary[] {
  if (changed.length === 0) return current;
  const byId = new Map(current.map((task) => [task.id, task]));
  for (const task of changed) byId.set(task.id, task);
  return [...byId.values()].sort(
    (a, b) =>
      (b.created_at ?? "").localeCompare(a.created_at ?? "") ||
      b.id.localeCompare(a.id)
  );
}

export interface SummaryState {
  summary: SummaryInfo | null;
  tasks: TaskSummary[];
  error: string | null;
  loading: boolean;
  loaded: boolean;
  reload: () => void;
}

export function useSummary({
  today,
  includeTasks = true,
  includeArchived = false,
  refreshMs = 30_000,
}: {
  today?: string;
  includeTasks?: boolean;
  includeArchived?: boolean;
  refreshMs?: number;
} = {}): SummaryState {
  const [summary, setSummary] = useState<SummaryInfo | null>(null);
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const seq = useRef(0);
  // The watermark is only valid for the query it was captured under.
  const watermark = useRef<{ key: string; since: string | null }>({
    key: "",
    since: null,
  });
  const queryKey = `${today ?? ""}|${includeTasks}|${includeArchived}`;

  const reload = useCallback(() => {
    if (watermark.current.key !== queryKey) {
      watermark.current = { key: queryKey, since: null };
    }
    const ticket = ++seq.current;
    setLoading(true);
    void fetchSummary({
      since: watermark.current.since,
      today,
      includeTasks,
      includeArchived,
    })
      .then((value) => {
        if (ticket !== seq.current) return;
        watermark.current.since = value.generated_at;
        setSummary(value);
        if (value.tasks !== null) {
          const rows = value.tasks;
          setTasks((current) =>
            value.partial ? mergeTasks(current, rows) : rows
          );
        }
        setError(null);
      })
      .catch((reason) => {
        if (ticket !== seq.current) return;
        setError(readErrorMessage(reason));
      })
      .finally(() => {
        if (ticket === seq.current) setLoading(false);
      });
  }, [queryKey, today, includeTasks, includeArchived]);

  useEffect(() => {
    reload();
    const timer = setInterval(reload, refreshMs);
    return () => clearInterval(timer);
  }, [reload, refreshMs]);

  return { summary, tasks, error, loading, loaded: summary !== null, reload };
}
