import { useCallback, useEffect, useState } from "react";
import { Crown, ExternalLink, Eye, Link2, Zap } from "lucide-react";
import { api, ApiError } from "../api";
import type { ActorInfo, ApprovalInfo, Me, RuntimeSessionInfo, Status, Task } from "../types";
import { GATE_LABEL, PRIORITY_LABEL, STATUS_LABEL, TRANSITIONS } from "../types";
import { taskDeepLink } from "../deeplink";

interface Props {
  taskId: string;
  me: Me;
  actors: ActorInfo[];
  onClose: () => void;
  onChanged: () => void;
  /** When present, upstream/downstream relations render as jump links. */
  onOpenTask?: (taskId: string) => void;
}

export default function TaskDrawer({ taskId, me, actors, onClose, onChanged, onOpenTask }: Props) {
  const [task, setTask] = useState<Task | null>(null);
  const [note, setNote] = useState("");
  const [holder, setHolder] = useState("");
  const [progress, setProgress] = useState(0);
  const [dueAt, setDueAt] = useState("");
  const [approvals, setApprovals] = useState<ApprovalInfo[]>([]);
  const [sessions, setSessions] = useState<RuntimeSessionInfo[]>([]);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);

  const loadSeq = useState(() => ({ current: 0 }))[0];

  const load = useCallback(async () => {
    const seq = ++loadSeq.current;
    try {
      const fresh = await api.get<Task>(`/api/tasks/${taskId}`);
      if (seq !== loadSeq.current) return;
      setTask(fresh);
      setHolder(fresh.holder);
      setProgress(fresh.progress);
      setDueAt(fresh.due_at ?? "");
      if (fresh.pipeline) {
        void api
          .get<ApprovalInfo[]>(`/api/approvals?task_id=${taskId}`)
          .then((rows) => {
            if (seq === loadSeq.current) setApprovals(rows);
          })
          .catch(() => undefined);
      }
      // Related sessions feed the execution timeline; a failure here must not
      // block the drawer itself, so degrade to attempts-only.
      void api
        .get<RuntimeSessionInfo[]>(`/api/sessions?task_id=${taskId}`)
        .then((rows) => {
          if (seq === loadSeq.current) setSessions(rows);
        })
        .catch(() => {
          if (seq === loadSeq.current) setSessions([]);
        });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "加载失败");
    }
  }, [taskId, loadSeq]);

  useEffect(() => {
    void load();
  }, [load]);

  // Keyboard: Escape closes the drawer from anywhere inside it.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  async function copyLink() {
    const link = taskDeepLink(taskId);
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      window.prompt("复制此任务卡链接：", link);
    }
  }

  async function apply(body: Record<string, unknown>) {
    await apply2(`/api/tasks/${taskId}/update`, body);
  }

  async function apply2(path: string, body: Record<string, unknown>) {
    setBusy(true);
    setError("");
    try {
      await api.post(path, body);
      setNote("");
      await load();
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "操作失败");
    } finally {
      setBusy(false);
    }
  }

  if (!task) {
    return (
      <div className="drawer-mask" onClick={onClose}>
        <aside className="drawer" onClick={(e) => e.stopPropagation()}>
          {error ? <p className="error">{error}</p> : <p>加载中…</p>}
        </aside>
      </div>
    );
  }

  const currentStage = task.pipeline?.[task.pipeline_stage];
  const transitions = task.proposal
    ? task.status === "queued" ? (["cancelled"] as Status[]) : []
    : !task.pipeline
    ? TRANSITIONS[task.status]
    : currentStage?.gate === "queen"
      ? []
      : TRANSITIONS[task.status].filter(
          (to) =>
            (to === "doing" && ["queued", "handoff", "blocked"].includes(task.status)) ||
            (task.status === "doing" && to === "blocked")
        );
  const requiredNote = note.trim() || undefined;

  function transition(to: Status) {
    const body: Record<string, unknown> = {
      status: to,
      note: requiredNote ?? `${STATUS_LABEL[task!.status]} → ${STATUS_LABEL[to]}`,
    };
    if (to === "blocked") {
      const reason = window.prompt("受阻原因(必填):");
      if (!reason || !reason.trim()) return;
      body.blocked_reason = reason.trim();
    }
    void apply(body);
  }

  return (
    <div className="drawer-mask" onClick={onClose}>
      <aside className="drawer" onClick={(e) => e.stopPropagation()}>
        <header className="drawer-head">
          <div>
            <span className={`chip chip-status-${task.status}`}>{STATUS_LABEL[task.status]}</span>
            <h2>{task.title}</h2>
            <p className="drawer-sub">
              {task.id} · 发单 {task.created_by}
              {task.dept ? ` · ${task.dept}` : ""} · 优先级 {PRIORITY_LABEL[task.priority]}
              {task.status === "doing" && ` · 进度 ${task.progress}%`}
              {task.due_at && ` · 截止 ${task.due_at}`}
              {task.open_dispatch && " · 挂单待接"}
            </p>
          </div>
          <button
            className="drawer-link"
            onClick={() => void copyLink()}
            title="复制本卡链接"
            aria-label="复制本卡链接"
          >
            <Link2 size={15} />
            {copied && <span className="drawer-link__hint">已复制</span>}
          </button>
          <button className="close" onClick={onClose} aria-label="关闭任务详情">
            ×
          </button>
        </header>

        {task.blocked_reason && <p className="blocked-banner">受阻:{task.blocked_reason}</p>}

        {task.proposal && (
          <section className="drawer-section proposal-section">
            <h3>名册变更提案</h3>
            <p className="muted">批准后才会创建以下实体；本卡不包含观察目录的位置。</p>
            <ul>
              {task.proposal.items.map((item) => (
                <li key={item.key}>
                  <strong>{item.kind.replace("_", " ")} · {item.identity}</strong>
                  <span>
                    {Object.entries(item.fields)
                      .filter(([name]) => !["legacy_ref"].includes(name))
                      .map(([name, value]) => `${name}=${Array.isArray(value) ? value.join(", ") : String(value)}`)
                      .join(" · ")}
                  </span>
                </li>
              ))}
            </ul>
            {task.status === "queued" && me.role === "admin" && me.actor_id === task.holder && (
              <div className="approve-bar">
                <span><Crown size={13} /> 由 {task.holder} 授权，服务端自动执行</span>
                <button
                  className="rt-button rt-button--primary"
                  disabled={busy}
                  onClick={() => void apply2(`/api/tasks/${taskId}/apply-proposal`, {})}
                >
                  批准并应用
                </button>
              </div>
            )}
          </section>
        )}

        {(task.blocked_by.length > 0 || task.blocks.length > 0) && (
          <section className="drawer-section dependency-section">
            <h3>任务依赖</h3>
            {task.blocked_by.length > 0 && (
              <div>
                <strong>开始前必须完成（上游）</strong>
                <ul>
                  {task.blocked_by.map((item) => (
                    <li key={item.id}>
                      {onOpenTask ? (
                        <button
                          type="button"
                          className="dependency-link"
                          onClick={() => onOpenTask(item.id)}
                        >
                          {item.id} · {item.title} · {STATUS_LABEL[item.status]}
                        </button>
                      ) : (
                        <>
                          {item.id} · {item.title} · {STATUS_LABEL[item.status]}
                        </>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {task.blocks.length > 0 && (
              <div>
                <strong>本卡完成后解除（下游）</strong>
                <ul>
                  {task.blocks.map((item) => (
                    <li key={item.id}>
                      {onOpenTask ? (
                        <button
                          type="button"
                          className="dependency-link"
                          onClick={() => onOpenTask(item.id)}
                        >
                          {item.id} · {item.title} · {STATUS_LABEL[item.status]}
                        </button>
                      ) : (
                        <>
                          {item.id} · {item.title} · {STATUS_LABEL[item.status]}
                        </>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </section>
        )}

        {task.pipeline && (
          <section className="drawer-section">
            <h3>流程节点</h3>
            <ol className="flow-stages">
              {task.pipeline.map((stage, index) => {
                const state =
                  task.status === "done"
                    ? "is-past"
                    : index < task.pipeline_stage
                      ? "is-past"
                      : index === task.pipeline_stage
                        ? "is-now"
                        : "";
                return (
                  <li key={index} className={state}>
                    <span className="flow-gate">
                      {stage.gate === "queen" ? (
                        <Crown size={12} />
                      ) : stage.gate === "review" ? (
                        <Eye size={12} />
                      ) : (
                        <Zap size={12} />
                      )}
                    </span>
                    <strong>{stage.name}</strong>
                    <em>
                      {actors.find((a) => a.id === stage.holder)?.display_name || stage.holder}
                      · {GATE_LABEL[stage.gate]}
                    </em>
                  </li>
                );
              })}
            </ol>
            {(() => {
              const pending = approvals.find((a) => a.status === "pending");
              if (pending && me.role === "admin") {
                return (
                  <div className="approve-bar">
                    <span>
                      <Crown size={13} /> 人工审批待处理:「{pending.stage_name}」
                    </span>
                    <button
                      className="rt-button rt-button--primary"
                      disabled={busy}
                      onClick={() =>
                        void apply2(`/api/approvals/${pending.id}/decide`, {
                          decision: "approve",
                          note: note.trim(),
                        })
                      }
                    >
                      批准
                    </button>
                    <button
                      disabled={busy}
                      onClick={() => {
                        const reason = note.trim() || window.prompt("驳回原因:") || "";
                        if (!reason) return;
                        void apply2(`/api/approvals/${pending.id}/decide`, {
                          decision: "reject",
                          note: reason,
                        });
                      }}
                    >
                      驳回
                    </button>
                  </div>
                );
              }
              return null;
            })()}
            {task.status === "doing" &&
              task.pipeline[task.pipeline_stage] &&
              task.pipeline[task.pipeline_stage].gate !== "queen" && (
                <div className="actions" style={{ marginTop: 10 }}>
                  <button
                    className="rt-button rt-button--primary"
                    disabled={busy}
                    onClick={() => {
                      const receipt = note.trim() || window.prompt("交棒回执(必填):") || "";
                      if (!receipt) return;
                      void apply2(`/api/tasks/${taskId}/stage-done`, { note: receipt });
                    }}
                  >
                    完成本节点,交棒 →
                  </button>
                  {task.pipeline[task.pipeline_stage].gate === "review" &&
                    task.pipeline_stage > 0 && (
                      <button
                        disabled={busy}
                        onClick={() => {
                          const reason = note.trim() || window.prompt("打回原因(必填):") || "";
                          if (!reason) return;
                          void apply2(`/api/tasks/${taskId}/stage-reject`, { note: reason });
                        }}
                      >
                        ← 打回上一节点
                      </button>
                    )}
                </div>
              )}
          </section>
        )}

        {task.acceptance.length > 0 && (
          <section className="drawer-section">
            <h3>验收标准</h3>
            <ul>
              {task.acceptance.map((item, index) => (
                <li key={index}>{item}</li>
              ))}
            </ul>
          </section>
        )}

        {task.refs.length > 0 && (
          <section className="drawer-section">
            <h3>交付物</h3>
            <ul>
              {task.refs.map((ref, index) => {
                const link = ref.startsWith("/") || ref.startsWith("http://") || ref.startsWith("https://");
                return (
                  <li key={ref}>
                    {link ? (
                      <a href={ref} target="_blank" rel="noreferrer">
                        <ExternalLink size={13} /> 查看交付物 {index + 1}
                      </a>
                    ) : (
                      ref
                    )}
                  </li>
                );
              })}
            </ul>
          </section>
        )}
        {!me.readonly && <section className="drawer-section">
          <h3>操作</h3>
          <textarea
            placeholder="备注(流转必填,留空则使用默认备注)"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            rows={2}
          />
          <div className="actions">
            {task.open_dispatch &&
              task.status === "queued" &&
              me.actor_id &&
              me.actor_id !== task.holder && (
              <button
                className="rt-button rt-button--primary"
                disabled={busy}
                onClick={() => {
                  setBusy(true);
                  api
                    .post(`/api/tasks/${taskId}/claim`, { note: note.trim() || "接单" })
                    .then(async () => {
                      setNote("");
                      await load();
                      onChanged();
                    })
                    .catch((err) =>
                      setError(err instanceof ApiError ? err.message : "接单失败")
                    )
                    .finally(() => setBusy(false));
                }}
              >
                接单
              </button>
            )}
            {transitions.map((to) => (
              <button key={to} disabled={busy} onClick={() => transition(to)}>
                → {STATUS_LABEL[to]}
              </button>
            ))}
            {transitions.length === 0 && (
              <span className="muted">
                {task.status === "done" || task.status === "cancelled"
                  ? "终态,无可用流转"
                  : "请使用上方流程操作"}
              </span>
            )}
          </div>
          {task.status === "doing" && (
            <div className="progress-report">
              <input
                type="range"
                min={0}
                max={100}
                step={5}
                value={progress}
                onChange={(e) => setProgress(Number(e.target.value))}
              />
              <span className="progress-report__value">{progress}%</span>
              <button
                disabled={busy || progress === task.progress}
                onClick={() =>
                  void apply({
                    progress,
                    note: requiredNote ?? `进度上报:${progress}%`,
                  })
                }
              >
                上报进度
              </button>
            </div>
          )}
          {!me.readonly && (
            <div className="due-editor">
              <label htmlFor={`due-${task.id}`}>截止日</label>
              <input
                id={`due-${task.id}`}
                type="date"
                value={dueAt}
                onChange={(e) => setDueAt(e.target.value)}
              />
              <button
                disabled={busy || dueAt === (task.due_at ?? "")}
                onClick={() =>
                  void apply({
                    due_at: dueAt,
                    note:
                      requiredNote ??
                      (dueAt ? `截止日定为 ${dueAt}` : "清除截止日"),
                  })
                }
              >
                保存截止日
              </button>
            </div>
          )}
          {!me.readonly && me.role !== "agent" && !task.pipeline && (
            <div className="reassign">
              <select value={holder} onChange={(e) => setHolder(e.target.value)}>
                {actors
                  .filter((a) => !a.disabled)
                  .map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.display_name || a.id}
                      {a.kind === "agent" ? "(智能体)" : ""}
                    </option>
                  ))}
              </select>
              <button
                disabled={busy || holder === task.holder}
                onClick={() =>
                  void apply({ holder, note: requiredNote ?? `改派持棒人 → ${holder}` })
                }
              >
                改派
              </button>
            </div>
          )}
          {error && <p className="error">{error}</p>}
        </section>}

        <section className="drawer-section attempts-section">
          <h3>执行时间线({task.attempts.length + sessions.length})</h3>
          {task.attempts.length === 0 && sessions.length === 0 ? (
            <p className="muted">尚无执行尝试或关联会话</p>
          ) : (
            <ol className="attempt-list">
              {([
                ...task.attempts.map((attempt) => ({
                  sortKey: attempt.ended_at || attempt.reported_at,
                  node: (
                    <li key={attempt.id} className={`attempt attempt--${attempt.outcome}`}>
                      <div className="attempt-head">
                        <strong>
                          {attempt.outcome === "failed"
                            ? "失败"
                            : attempt.outcome === "succeeded"
                              ? "成功"
                              : "已取消"}
                        </strong>
                        <span>
                          尝试 #{attempt.seq} · {attempt.reporter.kind} {attempt.reporter.id}
                          {attempt.reporter.duty ? ` · ${attempt.reporter.duty}` : ""}
                        </span>
                      </div>
                      {attempt.reason && <p className="attempt-reason">{attempt.reason}</p>}
                      <div className="attempt-meta">
                        <time>{attempt.started_at}</time>
                        <span>→</span>
                        <time>{attempt.ended_at}</time>
                        {attempt.exit_status !== null && (
                          <span>退出状态 {attempt.exit_status}</span>
                        )}
                      </div>
                    </li>
                  ),
                })),
                ...sessions.map((session) => ({
                  sortKey: session.updated_at ?? session.synced_at ?? "",
                  node: (
                    <li key={`session-${session.id}`} className="attempt attempt--session">
                      <div className="attempt-head">
                        <strong>会话</strong>
                        <span>
                          {session.actor_name} · {session.runtime}
                        </span>
                      </div>
                      <p className="attempt-reason">{session.title}</p>
                      <div className="attempt-meta">
                        {session.started_at && <time>{session.started_at}</time>}
                        {session.started_at && <span>→</span>}
                        <time>{session.updated_at ?? session.synced_at ?? ""}</time>
                        <span>{session.message_count} 条消息</span>
                      </div>
                    </li>
                  ),
                })),
              ]
                .sort((a, b) => (a.sortKey < b.sortKey ? 1 : -1))
                .map((entry) => entry.node))}
            </ol>
          )}
        </section>

        <section className="drawer-section">
          <h3>事件链({task.chain.length})</h3>
          <ol className="chain">
            {[...task.chain].reverse().map((event, index) => (
              <li key={index}>
                <div className="chain-head">
                  <strong>{event.who}</strong>
                  <time>{event.at}</time>
                </div>
                <div className="chain-body">
                  {event.did}
                  {event.payload.acted_on_behalf_of && (
                    <span className="chain-move">
                      执行 {event.payload.acted_on_behalf_of.performing_agent} · 代表 {event.payload.acted_on_behalf_of.authorising_identity}
                    </span>
                  )}
                  {event.from_status !== event.to_status && event.to_status && (
                    <span className="chain-move">
                      {event.from_status ? STATUS_LABEL[event.from_status] : "∅"} →{" "}
                      {STATUS_LABEL[event.to_status]}
                    </span>
                  )}
                  {event.from_holder !== event.to_holder && event.to_holder && (
                    <span className="chain-move">
                      持棒 {event.from_holder ?? "∅"} → {event.to_holder}
                    </span>
                  )}
                </div>
              </li>
            ))}
          </ol>
        </section>
      </aside>
    </div>
  );
}
