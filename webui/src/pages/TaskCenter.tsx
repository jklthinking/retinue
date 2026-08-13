import { useCallback, useEffect, useMemo, useState } from "react";
import type { Me, Status, TaskSummary } from "../types";
import { localTodayISO, PRIORITY_LABEL, STATUS_LABEL } from "../types";
import { useSummary } from "../lib/summary";
import { TASKS_CHANGED_EVENT } from "../components/DeepTaskDrawer";
import { Avatar } from "../avatar";
import { Ambient, DataState, PageHeader, Panel } from "../components/ui";
import { ListChecks } from "lucide-react";

const ALL_STATUS: Status[] = ["queued", "doing", "handoff", "blocked", "done", "cancelled"];

export default function TaskCenter({
  me,
  onOpenTask,
}: {
  me: Me;
  onOpenTask: (taskId: string) => void;
}) {
  // Incremental summary polling: the first load is a full snapshot, later
  // polls (and task-changed nudges) fetch only rows changed since the last
  // watermark and merge them into the cached list.
  const { summary, tasks, error, loaded, reload } = useSummary({
    today: localTodayISO(),
    includeArchived: true,
  });
  const actors = summary?.actors ?? [];
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<string>("");
  const [holder, setHolder] = useState<string>("");
  const today = localTodayISO();

  useEffect(() => {
    const onTasksChanged = () => reload();
    window.addEventListener(TASKS_CHANGED_EVENT, onTasksChanged);
    return () => window.removeEventListener(TASKS_CHANGED_EVENT, onTasksChanged);
  }, [reload]);

  const nameOf = useCallback(
    (id: string) => actors.find((a) => a.id === id)?.display_name || id,
    [actors]
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return tasks.filter(
      (t) =>
        (!status || t.status === status) &&
        (!holder || t.holder === holder) &&
        (!q ||
          t.title.toLowerCase().includes(q) ||
          t.id.includes(q) ||
          (t.dept ?? "").toLowerCase().includes(q))
    );
  }, [tasks, query, status, holder]);

  return (
    <div className="rt-page taskcenter-page">
      <Ambient />
      <PageHeader
        kicker="TASK CENTER · ALL RECORDS"
        title="任务中心"
        subtitle="全量任务记录:检索、筛选、追溯每一张卡的完整事件链。"
      />

      <Panel icon={<ListChecks size={15} />} kicker="RECORDS" title={`任务记录 ${filtered.length} / ${tasks.length}`}>
      {error && <DataState error={error} stale={loaded} onRetry={reload} />}
      <div className="tc-filters">
        <input
          placeholder="搜索标题 / 编号 / 条线…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">全部状态</option>
          {ALL_STATUS.map((s) => (
            <option key={s} value={s}>
              {STATUS_LABEL[s]}
            </option>
          ))}
        </select>
        <select value={holder} onChange={(e) => setHolder(e.target.value)}>
          <option value="">全部持棒人</option>
          {actors.map((a) => (
            <option key={a.id} value={a.id}>
              {a.display_name || a.id}
            </option>
          ))}
        </select>
      </div>

      <table className="admin-table tc-table">
        <thead>
          <tr>
            <th>编号</th>
            <th>标题</th>
            <th>状态</th>
            <th>持棒</th>
            <th>优先级</th>
            <th>条线</th>
            <th>截止</th>
            <th>最近更新</th>
          </tr>
        </thead>
        <tbody>
          {filtered.map((task) => (
            <tr
              key={task.id}
              className="tc-row"
              onClick={() => onOpenTask(task.id)}
              tabIndex={0}
              aria-label={`任务 ${task.id} ${task.title}`}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  onOpenTask(task.id);
                }
              }}
            >
              <td className="card-id">{task.id.replace("task-", "")}</td>
              <td className="tc-title">{task.title}</td>
              <td>
                <span className={`chip chip-status-${task.status}`}>
                  {STATUS_LABEL[task.status]}
                </span>
              </td>
              <td>
                <span className="holder-line">
                  <Avatar name={nameOf(task.holder)} size={18} square />
                  {nameOf(task.holder)}
                </span>
              </td>
              <td>{PRIORITY_LABEL[task.priority]}</td>
              <td>{task.dept ?? "—"}</td>
              <td
                className={`muted ${
                  task.due_at && task.due_at < today && !["done", "cancelled"].includes(task.status)
                    ? "tc-due-overdue"
                    : ""
                }`}
              >
                {task.due_at ?? "—"}
              </td>
              <td className="muted">
                {task.updated_at ? task.updated_at.slice(0, 16) : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {filtered.length === 0 && <p className="muted">没有匹配的任务</p>}
      </Panel>
    </div>
  );
}
