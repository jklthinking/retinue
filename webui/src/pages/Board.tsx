import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "../api";
import type { ActorInfo, Me, Status, TaskSummary } from "../types";
import { localTodayISO, PRIORITY_LABEL, STATUS_LABEL, TRANSITIONS } from "../types";
import { TASKS_CHANGED_EVENT } from "../components/DeepTaskDrawer";
import NewTaskDialog from "../components/NewTaskDialog";
import { Avatar } from "../avatar";
import { Ambient, DataState, PageHeader } from "../components/ui";
import { ListChecks, Plus } from "lucide-react";

const COLUMNS: Status[] = ["queued", "doing", "handoff", "blocked", "done"];

export default function Board({
  me,
  onOpenTask,
}: {
  me: Me;
  onOpenTask: (taskId: string) => void;
}) {
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [actors, setActors] = useState<ActorInfo[]>([]);
  const [readyIds, setReadyIds] = useState<Set<string>>(new Set());
  const [readyOnly, setReadyOnly] = useState(false);
  const [creating, setCreating] = useState(false);
  const [dragging, setDragging] = useState<TaskSummary | null>(null);
  const [error, setError] = useState("");
  const loadSeq = useRef(0);
  const today = localTodayISO();

  const load = useCallback(async () => {
    const seq = ++loadSeq.current;
    try {
      const [taskList, actorList, readyList] = await Promise.all([
        api.getAllPages<TaskSummary>("/api/tasks"),
        api.get<ActorInfo[]>("/api/actors"),
        api.get<TaskSummary[]>("/api/tasks/ready"),
      ]);
      if (seq !== loadSeq.current) return;
      setTasks(taskList);
      setActors(actorList);
      setReadyIds(new Set(readyList.map((task) => task.id)));
      setError("");
    } catch (err) {
      if (seq !== loadSeq.current) return;
      setError(err instanceof ApiError ? err.message : "加载失败");
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 30_000);
    const onTasksChanged = () => void load();
    window.addEventListener(TASKS_CHANGED_EVENT, onTasksChanged);
    return () => {
      clearInterval(timer);
      window.removeEventListener(TASKS_CHANGED_EVENT, onTasksChanged);
    };
  }, [load]);

  const byStatus = useMemo(() => {
    const groups: Record<Status, TaskSummary[]> = {
      queued: [],
      doing: [],
      handoff: [],
      blocked: [],
      done: [],
      cancelled: [],
    };
    for (const task of tasks) {
      if (!readyOnly || readyIds.has(task.id)) groups[task.status].push(task);
    }
    return groups;
  }, [tasks, readyIds, readyOnly]);

  const actorName = useCallback(
    (id: string) => actors.find((a) => a.id === id)?.display_name || id,
    [actors]
  );

  async function moveTask(task: TaskSummary, to: Status) {
    if (!TRANSITIONS[task.status].includes(to)) return;
    let blocked_reason: string | undefined;
    if (to === "blocked") {
      const reason = window.prompt("受阻原因(必填):");
      if (!reason || !reason.trim()) return;
      blocked_reason = reason.trim();
    }
    try {
      await api.post(`/api/tasks/${task.id}/update`, {
        status: to,
        note: `看板流转:${STATUS_LABEL[task.status]} → ${STATUS_LABEL[to]}`,
        ...(blocked_reason ? { blocked_reason } : {}),
      });
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "操作失败");
    }
  }

  return (
    <div className="rt-page board-page">
      <Ambient />
      <PageHeader
        kicker="TASK BOARD · KANBAN"
        title="任务看板"
        subtitle={me.readonly
          ? `${tasks.length} 张真实任务卡 · ${readyIds.size} 张现在可做 · 观察席只读`
          : `${tasks.length} 张任务卡 · ${readyIds.size} 张现在可做 · 拖拽卡片即可流转状态`}
        tools={
          <>
            <button
              className={readyOnly ? "rt-button rt-button--primary" : "rt-button"}
              onClick={() => setReadyOnly((value) => !value)}
            >
              <ListChecks size={14} /> {readyOnly ? "显示全部" : `现在可做 (${readyIds.size})`}
            </button>
            {me.readonly ? (
              <span className="chip chip-medium">只读实盘</span>
            ) : (
              <button className="rt-button rt-button--primary" onClick={() => setCreating(true)}>
                <Plus size={14} /> 新建任务
              </button>
            )}
          </>
        }
      />
      {error && (
        <DataState error={error} stale={tasks.length > 0} onRetry={() => void load()} />
      )}
      <div className="board">
        {COLUMNS.map((status) => {
          const droppable = !me.readonly && dragging !== null && TRANSITIONS[dragging.status].includes(status);
          return (
            <section
              key={status}
              className={`column column-${status} ${droppable ? "is-droppable" : ""}`}
              onDragOver={(e) => {
                if (droppable) e.preventDefault();
              }}
              onDrop={(e) => {
                e.preventDefault();
                if (dragging && droppable) void moveTask(dragging, status);
                setDragging(null);
              }}
            >
              <header>
                <span className={`dot dot-${status}`} />
                {STATUS_LABEL[status]}
                <em>{byStatus[status].length}</em>
              </header>
              <div className="cards">
                {byStatus[status].map((task) => (
                  <article
                    key={task.id}
                    className={`card priority-${task.priority}`}
                    draggable={!me.readonly}
                    onDragStart={() => !me.readonly && setDragging(task)}
                    onDragEnd={() => !me.readonly && setDragging(null)}
                    onClick={() => onOpenTask(task.id)}
                    tabIndex={0}
                    role="button"
                    aria-label={`任务卡 ${task.id} ${task.title}`}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        onOpenTask(task.id);
                      }
                    }}
                  >
                    <div className="card-title">{task.title}</div>
                    {task.status === "doing" && task.progress > 0 && (
                      <div className="rt-progress card-progress">
                        <span style={{ width: `${task.progress}%` }} />
                      </div>
                    )}
                    <div className="card-meta">
                      <span className="card-id">{task.id.replace("task-", "")}</span>
                      {task.open_dispatch && task.status === "queued" && (
                        <span className="chip chip-high hall-pulse">待接单</span>
                      )}
                      {task.pipeline && (
                        <span className="chip chip-medium">
                          流程 {Math.min(task.pipeline_stage + 1, task.pipeline.length)}/
                          {task.pipeline.length}
                        </span>
                      )}
                      {task.priority !== "none" && (
                        <span className={`chip chip-${task.priority}`}>
                          {PRIORITY_LABEL[task.priority]}
                        </span>
                      )}
                      {task.dept && <span className="chip chip-dept">{task.dept}</span>}
                      {task.due_at && (
                        <span
                          className={`chip ${
                            task.due_at < today
                              ? "chip-urgent"
                              : task.due_at === today
                                ? "chip-high"
                                : "chip-medium"
                          }`}
                          title={`截止 ${task.due_at}`}
                        >
                          {task.due_at < today
                            ? `逾期 ${task.due_at.slice(5)}`
                            : task.due_at === today
                              ? "今日到期"
                              : `截止 ${task.due_at.slice(5)}`}
                        </span>
                      )}
                    </div>
                    {(task.blocked_by.length > 0 || task.blocks.length > 0) && (
                      <div className="card-relations">
                        {task.blocked_by.length > 0 && (
                          <span title={task.blocked_by.map((item) => `${item.id} · ${item.title}`).join("\n")}>
                            前置: {task.blocked_by.map((item) => item.id).join(", ")}
                          </span>
                        )}
                        {task.blocks.length > 0 && (
                          <span title={task.blocks.map((item) => `${item.id} · ${item.title}`).join("\n")}>
                            后续: {task.blocks.map((item) => item.id).join(", ")}
                          </span>
                        )}
                      </div>
                    )}
                    <div className="card-holder">
                      <span className="holder-line">
                        <Avatar name={actorName(task.holder)} size={18} square />
                        {actorName(task.holder)}
                      </span>
                      {task.blocked_reason && (
                        <span className="blocked-note" title={task.blocked_reason}>
                          ⚠ {task.blocked_reason}
                        </span>
                      )}
                    </div>
                  </article>
                ))}
                {byStatus[status].length === 0 && <div className="empty">暂无</div>}
              </div>
            </section>
          );
        })}
      </div>
      {creating && !me.readonly && (
        <NewTaskDialog
          actors={actors}
          onClose={() => setCreating(false)}
          onCreated={() => {
            setCreating(false);
            void load();
          }}
        />
      )}
    </div>
  );
}
