import { useCallback, useEffect, useRef, useState } from "react";
import { BOARD_REFRESH_MS, DATA_REFRESH_EVENT } from "../lib/refresh";
import {
  AlarmClock,
  CalendarClock,
  Crown,
  ShieldAlert,
  WifiOff,
} from "lucide-react";
import { readErrorMessage } from "../api";
import { fetchSummary } from "../lib/summary";
import type { TaskSummary } from "../types";
import { localTodayISO } from "../types";
import { Avatar } from "../avatar";

/** First-screen action queue: five lanes served by one summary endpoint, so
 * the first paint never waits on a full task-table transfer. The aggregate
 * payload is bounded (counts plus the first rows per lane), and stale data
 * stays visible with a marker until a retry succeeds. */

interface Source<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
}

function useSource<T>(fetcher: () => Promise<T>, refreshMs = BOARD_REFRESH_MS): Source<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const seq = useRef(0);

  const reload = useCallback(() => {
    const ticket = ++seq.current;
    setLoading(true);
    void fetcher()
      .then((value) => {
        if (ticket !== seq.current) return;
        setData(value);
        setError(null);
      })
      .catch((reason) => {
        if (ticket !== seq.current) return;
        setError(readErrorMessage(reason));
      })
      .finally(() => {
        if (ticket === seq.current) setLoading(false);
      });
    // fetcher identity changes only when its own dependencies change.
  }, [fetcher]);

  useEffect(() => {
    reload();
    const timer = setInterval(reload, refreshMs);
    const onManual = () => reload();
    window.addEventListener(DATA_REFRESH_EVENT, onManual);
    return () => {
      clearInterval(timer);
      window.removeEventListener(DATA_REFRESH_EVENT, onManual);
    };
  }, [reload, refreshMs]);

  return { data, error, loading, reload };
}

function Lane({
  icon,
  title,
  count,
  tone,
  loading,
  error,
  stale,
  onRetry,
  empty,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  count: number;
  tone: "red" | "amber" | "blue" | "ink";
  loading: boolean;
  error: string | null;
  stale: boolean;
  onRetry: () => void;
  empty: string;
  children: React.ReactNode;
}) {
  return (
    <section className={`rt-lane rt-lane--${tone}`} aria-label={title}>
      <header className="rt-lane__head">
        {icon}
        <strong>{title}</strong>
        <em>{count}</em>
        {stale && error && <span className="rt-lane__stale">数据可能已过期</span>}
      </header>
      {error && !stale && (
        <div className="rt-lane__error" role="alert">
          <span>{error}</span>
          <button type="button" className="rt-button rt-button--soft" onClick={onRetry}>
            重试
          </button>
        </div>
      )}
      {!error && loading && !stale && <p className="rt-lane__empty">读取中…</p>}
      {(stale || (!error && !loading)) &&
        (count > 0 ? children : <p className="rt-lane__empty">{empty}</p>)}
    </section>
  );
}

function TaskRow({
  task,
  extra,
  onOpenTask,
}: {
  task: TaskSummary;
  extra?: string;
  onOpenTask: (taskId: string) => void;
}) {
  return (
    <button
      type="button"
      className="rt-lane__item"
      onClick={() => onOpenTask(task.id)}
      title={`${task.id} · ${task.title}`}
    >
      <span className="rt-lane__item-title">{task.title}</span>
      <span className="rt-lane__item-meta">
        {task.holder}
        {extra ? ` · ${extra}` : ""}
      </span>
    </button>
  );
}

export default function ActionQueue({
  onOpenTask,
}: {
  onOpenTask: (taskId: string) => void;
}) {
  // Lane items and counts come pre-computed from the summary endpoint; the
  // task list itself stays on the server (include_tasks=false).
  const summary = useSource(
    useCallback(
      () => fetchSummary({ today: localTodayISO(), includeTasks: false }),
      []
    )
  );

  const lanes = summary.data?.lanes ?? null;
  const stale = summary.data !== null;

  // A failed summary fetch with nothing cached degrades the whole queue to a
  // single retry affordance; once data exists, lanes keep rendering it with
  // per-lane stale markers instead.
  if (summary.error && !stale) {
    return (
      <div className="rt-queue" aria-label="行动队列">
        <div className="rt-lane__error" role="alert">
          <span>{summary.error}</span>
          <button type="button" className="rt-button rt-button--soft" onClick={summary.reload}>
            重试
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="rt-queue" aria-label="行动队列">
      <Lane
        icon={<Crown size={14} />}
        title="等我决策"
        count={lanes?.decisions.count ?? 0}
        tone="amber"
        loading={summary.loading}
        error={summary.error}
        stale={stale}
        onRetry={summary.reload}
        empty="没有待决策事项"
      >
        {(lanes?.decisions.items ?? []).slice(0, 5).map((approval) => (
          <button
            key={approval.id}
            type="button"
            className="rt-lane__item"
            onClick={() => onOpenTask(approval.task_id)}
            title={`${approval.task_id} · ${approval.task_title ?? ""}`}
          >
            <span className="rt-lane__item-title">
              {approval.task_title || approval.task_id}
            </span>
            <span className="rt-lane__item-meta">
              {approval.stage_name ? `节点「${approval.stage_name}」` : "人工审批"} ·{" "}
              {approval.requested_by}
            </span>
          </button>
        ))}
      </Lane>

      <Lane
        icon={<CalendarClock size={14} />}
        title="今日到期"
        count={lanes?.due_today.count ?? 0}
        tone="blue"
        loading={summary.loading}
        error={summary.error}
        stale={stale}
        onRetry={summary.reload}
        empty="今天没有到期任务"
      >
        {(lanes?.due_today.items ?? []).slice(0, 5).map((task) => (
          <TaskRow key={task.id} task={task} extra="今日到期" onOpenTask={onOpenTask} />
        ))}
      </Lane>

      <Lane
        icon={<AlarmClock size={14} />}
        title="已逾期"
        count={lanes?.overdue.count ?? 0}
        tone="red"
        loading={summary.loading}
        error={summary.error}
        stale={stale}
        onRetry={summary.reload}
        empty="没有逾期任务"
      >
        {(lanes?.overdue.items ?? []).slice(0, 5).map((task) => (
          <TaskRow
            key={task.id}
            task={task}
            extra={`截止 ${task.due_at}`}
            onOpenTask={onOpenTask}
          />
        ))}
      </Lane>

      <Lane
        icon={<ShieldAlert size={14} />}
        title="阻塞"
        count={lanes?.blocked.count ?? 0}
        tone="red"
        loading={summary.loading}
        error={summary.error}
        stale={stale}
        onRetry={summary.reload}
        empty="没有阻塞任务"
      >
        {(lanes?.blocked.items ?? []).slice(0, 5).map((task) => (
          <TaskRow
            key={task.id}
            task={task}
            extra={task.blocked_reason ?? undefined}
            onOpenTask={onOpenTask}
          />
        ))}
      </Lane>

      <Lane
        icon={<WifiOff size={14} />}
        title="失联执行者"
        count={lanes?.lost_executors.count ?? 0}
        tone="ink"
        loading={summary.loading}
        error={summary.error}
        stale={stale}
        onRetry={summary.reload}
        empty="在制卡的执行者都在线"
      >
        {(lanes?.lost_executors.items ?? []).slice(0, 5).map(({ task, actor }) => (
          <button
            key={task.id}
            type="button"
            className="rt-lane__item"
            onClick={() => onOpenTask(task.id)}
            title={`${task.id} · ${task.title}`}
          >
            <span className="rt-lane__item-title">
              <Avatar name={actor.display_name || actor.id} size={16} square />{" "}
              {actor.display_name || actor.id}
            </span>
            <span className="rt-lane__item-meta">
              {task.title}
              {actor.last_seen_at
                ? ` · 最后在线 ${actor.last_seen_at.slice(5, 16).replace("T", " ")}`
                : " · 从未在线"}
            </span>
          </button>
        ))}
      </Lane>
    </div>
  );
}
