import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlarmClock,
  CalendarClock,
  Check,
  CircleCheckBig,
  Inbox,
  SquareKanban,
  Users,
  X,
} from "lucide-react";
import { readErrorMessage } from "../api";
import {
  completeTodoItem,
  confirmTodoProposal,
  fetchTodoHome,
  fetchTodos,
  rejectTodoProposal,
  snoozeTodoItem,
} from "../lib/todos";
import type { TodoHome, TodoItem, TodoProposal, TodoWaitingItem } from "../types";
import type { Status } from "../types";
import { STATUS_LABEL } from "../types";
import { Ambient, PageHeader } from "../components/ui";
import { useVocab } from "../theme";

/** Personal affairs hub: one aggregate fetch (/api/todos/home) plus the full
 * item list for the promoted lane. All mutations go through the existing todo
 * endpoints and trigger a reload of both sources. */

interface AffairsData {
  home: TodoHome;
  promoted: TodoItem[];
}

function localDateISO(date: Date): string {
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}

function tomorrowISO(): string {
  const date = new Date();
  date.setDate(date.getDate() + 1);
  return localDateISO(date);
}

function Lane({
  icon,
  title,
  count,
  tone,
  empty,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  count: number;
  tone: "red" | "amber" | "blue" | "ink";
  empty: string;
  children: React.ReactNode;
}) {
  return (
    <section className={`rt-lane rt-lane--${tone}`} aria-label={title}>
      <header className="rt-lane__head">
        {icon}
        <strong>{title}</strong>
        <em>{count}</em>
      </header>
      {count > 0 ? children : <p className="rt-lane__empty">{empty}</p>}
    </section>
  );
}

function statusLabel(status: string): string {
  return STATUS_LABEL[status as Status] ?? status;
}

export default function Affairs({
  onOpenTask,
}: {
  onOpenTask: (taskId: string) => void;
}) {
  const vocab = useVocab();
  const [data, setData] = useState<AffairsData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionError, setActionError] = useState<string | null>(null);
  const [pendingKey, setPendingKey] = useState<string | null>(null);
  const [rejectingId, setRejectingId] = useState<string | null>(null);
  const [rejectNote, setRejectNote] = useState("");
  const [snoozingId, setSnoozingId] = useState<string | null>(null);
  const [snoozeDate, setSnoozeDate] = useState(tomorrowISO());
  const seq = useRef(0);

  const load = useCallback(() => {
    const ticket = ++seq.current;
    void Promise.all([fetchTodoHome(), fetchTodos()])
      .then(([home, todos]) => {
        if (ticket !== seq.current) return;
        setData({
          home,
          promoted: todos.filter(
            (item) => item.status === "promoted" && item.task_id !== null
          ),
        });
        setError(null);
      })
      .catch((reason) => {
        if (ticket !== seq.current) return;
        setError(readErrorMessage(reason));
      })
      .finally(() => {
        if (ticket === seq.current) setLoading(false);
      });
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, 30_000);
    return () => clearInterval(timer);
  }, [load]);

  /** Run one mutation, then refresh both lanes; failures surface as an alert. */
  const runAction = useCallback(
    (key: string, action: () => Promise<unknown>) => {
      setPendingKey(key);
      setActionError(null);
      void action()
        .then(() => {
          setRejectingId(null);
          setRejectNote("");
          setSnoozingId(null);
          load();
        })
        .catch((reason) => setActionError(readErrorMessage(reason)))
        .finally(() => setPendingKey(null));
    },
    [load]
  );

  const home = data?.home ?? null;

  const proposalRow = (proposal: TodoProposal) => (
    <div key={proposal.id} className="rt-lane__item rt-lane__item--static">
      <span className="rt-lane__item-title">{proposal.title}</span>
      <span className="rt-lane__item-meta">
        {proposal.proposed_by}
        {proposal.source_channel ? ` · 来自 ${proposal.source_channel}` : ""}
        {proposal.due_at ? ` · 截止 ${proposal.due_at}` : ""}
      </span>
      {rejectingId === proposal.id ? (
        <form
          className="rt-lane__form"
          onSubmit={(event) => {
            event.preventDefault();
            runAction(`reject:${proposal.id}`, () =>
              rejectTodoProposal(proposal.id, rejectNote)
            );
          }}
        >
          <input
            type="text"
            value={rejectNote}
            maxLength={240}
            placeholder="驳回理由（可选）"
            aria-label={`驳回理由:${proposal.title}`}
            onChange={(event) => setRejectNote(event.target.value)}
          />
          <button
            type="submit"
            className="rt-button rt-button--soft"
            disabled={pendingKey !== null}
          >
            <Check size={13} /> 确认驳回
          </button>
          <button
            type="button"
            className="rt-button rt-button--soft"
            onClick={() => {
              setRejectingId(null);
              setRejectNote("");
            }}
          >
            <X size={13} /> 取消
          </button>
        </form>
      ) : (
        <span className="rt-lane__actions">
          <button
            type="button"
            className="rt-button rt-button--soft"
            disabled={pendingKey !== null}
            onClick={() =>
              runAction(`confirm:${proposal.id}`, () =>
                confirmTodoProposal(proposal.id)
              )
            }
          >
            <CircleCheckBig size={13} /> 确认
          </button>
          <button
            type="button"
            className="rt-button rt-button--soft"
            disabled={pendingKey !== null}
            onClick={() => {
              setRejectingId(proposal.id);
              setRejectNote("");
            }}
          >
            <X size={13} /> 驳回
          </button>
        </span>
      )}
    </div>
  );

  const itemRow = (item: TodoItem, overdue: boolean) => (
    <div key={item.id} className="rt-lane__item rt-lane__item--static">
      <span className="rt-lane__item-title">{item.title}</span>
      <span className="rt-lane__item-meta">
        {overdue && item.due_at ? `截止 ${item.due_at}` : "今日到期"}
        {item.status === "snoozed" ? " · 已延期过" : ""}
      </span>
      {snoozingId === item.id ? (
        <form
          className="rt-lane__form"
          onSubmit={(event) => {
            event.preventDefault();
            if (!snoozeDate) return;
            runAction(`snooze:${item.id}`, () =>
              snoozeTodoItem(item.id, snoozeDate)
            );
          }}
        >
          <input
            type="date"
            required
            value={snoozeDate}
            aria-label={`延期到:${item.title}`}
            onChange={(event) => setSnoozeDate(event.target.value)}
          />
          <button
            type="submit"
            className="rt-button rt-button--soft"
            disabled={pendingKey !== null || !snoozeDate}
          >
            <Check size={13} /> 确定延期
          </button>
          <button
            type="button"
            className="rt-button rt-button--soft"
            onClick={() => setSnoozingId(null)}
          >
            <X size={13} /> 取消
          </button>
        </form>
      ) : (
        <span className="rt-lane__actions">
          <button
            type="button"
            className="rt-button rt-button--soft"
            disabled={pendingKey !== null}
            onClick={() =>
              runAction(`complete:${item.id}`, () => completeTodoItem(item.id))
            }
          >
            <CircleCheckBig size={13} /> 完成
          </button>
          <button
            type="button"
            className="rt-button rt-button--soft"
            disabled={pendingKey !== null}
            onClick={() => {
              setSnoozingId(item.id);
              setSnoozeDate(tomorrowISO());
            }}
          >
            <CalendarClock size={13} /> 延期
          </button>
        </span>
      )}
    </div>
  );

  const waitingRow = (item: TodoWaitingItem) => (
    <button
      key={item.id}
      type="button"
      className="rt-lane__item"
      onClick={() => item.task_id && onOpenTask(item.task_id)}
      title={item.task_id ?? item.id}
    >
      <span className="rt-lane__item-title">{item.title}</span>
      <span className="rt-lane__item-meta">
        {item.task_holder ? `${item.task_holder} 持有` : "等待认领"} ·{" "}
        {statusLabel(item.task_status)}
      </span>
    </button>
  );

  const promotedRow = (item: TodoItem) => (
    <button
      key={item.id}
      type="button"
      className="rt-lane__item"
      onClick={() => item.task_id && onOpenTask(item.task_id)}
      title={item.task_id ?? item.id}
    >
      <span className="rt-lane__item-title">{item.title}</span>
      <span className="rt-lane__item-meta">{item.task_id}</span>
    </button>
  );

  return (
    <div className="rt-page">
      <Ambient />
      <PageHeader
        kicker={vocab.affairsEyebrow}
        title={vocab.affairsTitle}
        subtitle={vocab.affairsSubtitle}
      />

      {loading && !data && <p className="rt-data-state">正在读取数据…</p>}
      {error && (
        <div className="rt-data-state rt-data-state--error" role="alert">
          <strong>{data ? "读取失败，数据可能已过期" : "读取失败"}</strong>
          <span>{error}</span>
          <button className="rt-button rt-button--soft" onClick={load}>
            重试
          </button>
        </div>
      )}
      {actionError && (
        <div className="rt-data-state rt-data-state--error" role="alert">
          <strong>操作失败</strong>
          <span>{actionError}</span>
        </div>
      )}

      {home && (
        <div className="rt-queue" aria-label={vocab.affairsTitle}>
          <Lane
            icon={<Inbox size={14} />}
            title={vocab.affairsPendingProposals}
            count={home.pending_proposals.length}
            tone="amber"
            empty="没有待确认的提案"
          >
            {home.pending_proposals.map(proposalRow)}
          </Lane>

          <Lane
            icon={<CalendarClock size={14} />}
            title={vocab.affairsDueToday}
            count={home.due_today.length}
            tone="blue"
            empty="今天没有到期事项"
          >
            {home.due_today.map((item) => itemRow(item, false))}
          </Lane>

          <Lane
            icon={<AlarmClock size={14} />}
            title={vocab.affairsOverdue}
            count={home.overdue.length}
            tone="red"
            empty="没有逾期事项"
          >
            {home.overdue.map((item) => itemRow(item, true))}
          </Lane>

          <Lane
            icon={<Users size={14} />}
            title={vocab.affairsWaiting}
            count={home.waiting_on_others.length}
            tone="ink"
            empty="没有等待他人的事项"
          >
            {home.waiting_on_others.map(waitingRow)}
          </Lane>

          <Lane
            icon={<SquareKanban size={14} />}
            title={vocab.affairsPromoted}
            count={data?.promoted.length ?? 0}
            tone="ink"
            empty="还没有升级到共享看板的事项"
          >
            {(data?.promoted ?? []).map(promotedRow)}
          </Lane>
        </div>
      )}
    </div>
  );
}
