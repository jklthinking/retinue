import { useCallback, useEffect, useRef, useState } from "react";
import { BOARD_REFRESH_MS, DATA_REFRESH_EVENT } from "../lib/refresh";
import { AlarmClock, ClipboardCheck, Crown, ShieldAlert } from "lucide-react";
import { readErrorMessage } from "../api";
import { fetchInbox } from "../lib/inbox";
import { useVocab } from "../theme";

/** Inbox swimlane above the first screen: four attention lanes (pending
 * decisions, QC replies, blocked, stale) served by one inbox endpoint. Lane
 * names come from the theme vocabulary so the interface voice stays a
 * configuration choice; the stale-data and error degradation contract matches
 * the action queue below it. */

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

const STALE_REASON_LABEL: Record<string, string> = {
  overdue: "已逾期",
  heartbeat_lost: "心跳丢失",
};

export default function InboxLanes({
  onOpenTask,
}: {
  onOpenTask: (taskId: string) => void;
}) {
  const vocab = useVocab();
  const inbox = useSource(useCallback(() => fetchInbox(), []));

  const lanes = inbox.data?.lanes ?? null;
  const stale = inbox.data !== null;

  if (inbox.error && !stale) {
    return (
      <div className="rt-queue" aria-label={vocab.inboxLabel}>
        <div className="rt-lane__error" role="alert">
          <span>{inbox.error}</span>
          <button type="button" className="rt-button rt-button--soft" onClick={inbox.reload}>
            重试
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="rt-queue" aria-label={vocab.inboxLabel}>
      <Lane
        icon={<Crown size={14} />}
        title={vocab.inboxDecisions}
        count={lanes?.decisions.count ?? 0}
        tone="amber"
        loading={inbox.loading}
        error={inbox.error}
        stale={stale}
        onRetry={inbox.reload}
        empty="没有待拍板事项"
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
        icon={<ClipboardCheck size={14} />}
        title={vocab.inboxReviews}
        count={lanes?.reviews.count ?? 0}
        tone="blue"
        loading={inbox.loading}
        error={inbox.error}
        stale={stale}
        onRetry={inbox.reload}
        empty="没有待回复的质检意见"
      >
        {(lanes?.reviews.items ?? []).slice(0, 5).map((review) => (
          <button
            key={review.review_id}
            type="button"
            className="rt-lane__item"
            onClick={() => onOpenTask(review.task_id)}
            title={`${review.task_id} · ${review.body}`}
          >
            <span className="rt-lane__item-title">
              {review.task_title || review.task_id}
            </span>
            <span className="rt-lane__item-meta">
              {review.author} · {review.created_at.slice(5, 16).replace("T", " ")}
            </span>
          </button>
        ))}
      </Lane>

      <Lane
        icon={<ShieldAlert size={14} />}
        title={vocab.inboxBlocked}
        count={lanes?.blocked.count ?? 0}
        tone="red"
        loading={inbox.loading}
        error={inbox.error}
        stale={stale}
        onRetry={inbox.reload}
        empty="没有阻塞任务"
      >
        {(lanes?.blocked.items ?? []).slice(0, 5).map((task) => (
          <button
            key={task.id}
            type="button"
            className="rt-lane__item"
            onClick={() => onOpenTask(task.id)}
            title={`${task.id} · ${task.title}`}
          >
            <span className="rt-lane__item-title">{task.title}</span>
            <span className="rt-lane__item-meta">
              {task.holder}
              {task.blocked_reason ? ` · ${task.blocked_reason}` : ""}
            </span>
          </button>
        ))}
      </Lane>

      <Lane
        icon={<AlarmClock size={14} />}
        title={vocab.inboxStale}
        count={lanes?.stale.count ?? 0}
        tone="ink"
        loading={inbox.loading}
        error={inbox.error}
        stale={stale}
        onRetry={inbox.reload}
        empty="没有超期未动的在制卡"
      >
        {(lanes?.stale.items ?? []).slice(0, 5).map(({ task, reasons }) => (
          <button
            key={task.id}
            type="button"
            className="rt-lane__item"
            onClick={() => onOpenTask(task.id)}
            title={`${task.id} · ${task.title}`}
          >
            <span className="rt-lane__item-title">{task.title}</span>
            <span className="rt-lane__item-meta">
              {task.holder} ·{" "}
              {reasons.map((reason) => STALE_REASON_LABEL[reason] ?? reason).join(" · ")}
            </span>
          </button>
        ))}
      </Lane>
    </div>
  );
}
