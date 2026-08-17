import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowRight, Crown, Handshake, Megaphone, Rows3 } from "lucide-react";
import { api, ApiError } from "../api";
import type { ActorInfo, ApprovalInfo, Me, Task } from "../types";
import { PRIORITY_LABEL } from "../types";
import {
  claimedAt,
  doneAt,
  elapsedText,
  lastEvent,
  localDateKey,
  postedAt,
  stageIndex,
} from "../lib/collab";
import StageStepper from "../components/StageStepper";
import TaskDrawer from "../components/TaskDrawer";
import { Ambient, Metric, PageHeader, Panel } from "../components/ui";
import { Avatar } from "../avatar";
import { BOARD_REFRESH_MS, DATA_REFRESH_EVENT } from "../lib/refresh";

export default function Collab({ me }: { me: Me }) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [actors, setActors] = useState<ActorInfo[]>([]);
  const [approvals, setApprovals] = useState<ApprovalInfo[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [deciding, setDeciding] = useState<number | null>(null);

  const loadSeq = useState(() => ({ current: 0 }))[0];

  const load = useCallback(async (bypass = false) => {
    const seq = ++loadSeq.current;
    try {
      const [taskList, actorList, approvalList] = await Promise.all([
        api.getCached<Task[]>("/api/tasks", bypass),
        api.getCached<ActorInfo[]>("/api/actors", bypass),
        api.get<ApprovalInfo[]>("/api/approvals?pending=true").catch(() => []),
      ]);
      if (seq !== loadSeq.current) return; // stale poll: never clobber fresher state
      setTasks(taskList);
      setActors(actorList);
      setApprovals(approvalList);
      setError("");
    } catch {
      if (!tasks.length) setError("协作进度暂时拉不下来，已保留上次数据（如有）");
    }
  }, [loadSeq]);

  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), BOARD_REFRESH_MS);
    const onManual = () => void load(true);
    window.addEventListener(DATA_REFRESH_EVENT, onManual);
    return () => {
      clearInterval(timer);
      window.removeEventListener(DATA_REFRESH_EVENT, onManual);
    };
  }, [load]);

  const nameOf = useCallback(
    (id: string) => actors.find((a) => a.id === id)?.display_name || id,
    [actors]
  );

  const live = useMemo(() => tasks.filter((t) => t.status !== "cancelled"), [tasks]);
  const openTasks = live.filter((t) => t.open_dispatch && t.status === "queued");
  const claimedIdle = live.filter((t) => !t.open_dispatch && t.status === "queued");
  const working = live.filter((t) => t.status === "doing");
  const blocked = live.filter((t) => t.status === "blocked");
  const review = live.filter((t) => t.status === "handoff");
  const doneToday = live.filter(
    (t) => t.status === "done" && (doneAt(t) ?? "").slice(0, 10) === localDateKey()
  );
  const avgProgress = working.length
    ? Math.round(working.reduce((sum, t) => sum + t.progress, 0) / working.length)
    : 0;

  const tableRows = useMemo(() => {
    const active = live.filter((t) => t.status !== "done");
    const done = live
      .filter((t) => t.status === "done")
      .sort((a, b) => (doneAt(b) ?? "").localeCompare(doneAt(a) ?? ""))
      .slice(0, 6);
    active.sort(
      (a, b) =>
        stageIndex(a) - stageIndex(b) ||
        (b.updated_at ?? "").localeCompare(a.updated_at ?? "")
    );
    return [...active, ...done];
  }, [live]);

  async function claim(task: Task) {
    setError("");
    try {
      await api.post(`/api/tasks/${task.id}/claim`, {});
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "接单失败");
    }
  }

  async function decide(approvalId: number, decision: "approve" | "reject", note: string) {
    if (deciding !== null) return;
    setError("");
    setDeciding(approvalId);
    try {
      await api.post(`/api/approvals/${approvalId}/decide`, { decision, note });
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "裁决失败");
    } finally {
      setDeciding(null);
    }
  }

  const agents = actors.filter((a) => a.kind === "agent" && !a.disabled);
  const laneOf = (holder: string) => live.filter((t) => t.holder === holder);

  return (
    <div className="rt-page collab-page">
      <Ambient />
      <PageHeader
        kicker="MULTI-BOT COLLABORATION"
        title="协作进度"
        subtitle="发单 → 接单 → 执行 → 交付的完整流水线,每一棒都有回执。"
      />
      {error && <p className="error">{error}</p>}

      <div className="rt-metrics">
        <Metric icon={<Megaphone />} label="大厅待接" value={openTasks.length} sub="挂单等待认领" tone="amber" />
        <Metric icon={<Handshake />} label="已接未开工" value={claimedIdle.length} sub="接单待启动" tone="ink" />
        <Metric
          icon={<Rows3 />}
          label="执行中"
          value={
            <>
              {working.length}
              <em className="rt-metric__frac">均 {avgProgress}%</em>
            </>
          }
          sub={blocked.length > 0 ? `另有 ${blocked.length} 张受阻` : "全部顺畅"}
          tone={blocked.length > 0 ? "red" : "blue"}
        />
        <Metric icon={<ArrowRight />} label="交付审校" value={review.length} sub="等待验收" tone="teal" />
        <Metric icon={<Handshake />} label="今日完成" value={doneToday.length} sub="已交付验收" tone="green" />
      </div>

      <div className="rt-layout rt-layout--hero">
        <Panel icon={<Rows3 size={15} />} kicker="PIPELINE" title="任务进度表">
          <div className="collab-table-wrap">
            <table className="admin-table collab-table">
              <thead>
                <tr>
                  <th>任务</th>
                  <th>发单 → 接单</th>
                  <th>阶段</th>
                  <th>进度</th>
                  <th>最新回执</th>
                  <th>用时</th>
                </tr>
              </thead>
              <tbody>
                {tableRows.map((task) => {
                  const latest = lastEvent(task);
                  const claimed = claimedAt(task);
                  return (
                    <tr key={task.id} className="tc-row" onClick={() => setSelected(task.id)}>
                      <td>
                        <div className="collab-task-cell">
                          <strong>{task.title}</strong>
                          <small>
                            {task.id.replace("task-", "")}
                            {task.priority !== "none" && ` · ${PRIORITY_LABEL[task.priority]}`}
                            {task.dept ? ` · ${task.dept}` : ""}
                          </small>
                        </div>
                      </td>
                      <td>
                        <span className="collab-pair">
                          <Avatar name={nameOf(task.created_by)} size={20} square />
                          <ArrowRight size={11} className="collab-arrow" />
                          {task.open_dispatch && task.status === "queued" ? (
                            <span className="rt-badge rt-badge--warn">待接</span>
                          ) : (
                            <Avatar name={nameOf(task.holder)} size={20} square />
                          )}
                        </span>
                      </td>
                      <td>
                        <StageStepper task={task} />
                      </td>
                      <td className="collab-progress-cell">
                        <div className="rt-progress">
                          <span
                            className={
                              task.status === "blocked"
                                ? "is-red"
                                : task.progress >= 100
                                  ? "is-green"
                                  : ""
                            }
                            style={{ width: `${task.status === "done" ? 100 : task.progress}%` }}
                          />
                        </div>
                        <em>{task.status === "done" ? 100 : task.progress}%</em>
                      </td>
                      <td className="collab-note">
                        {latest ? (
                          <>
                            <span>{latest.did}</span>
                            <small>
                              {nameOf(latest.who)} · {latest.at.slice(5, 16).replace("T", " ")}
                            </small>
                          </>
                        ) : (
                          "—"
                        )}
                      </td>
                      <td className="collab-elapsed">
                        {elapsedText(claimed ?? postedAt(task), doneAt(task))}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {tableRows.length === 0 && <p className="muted">暂无任务</p>}
          </div>
        </Panel>

        <div className="collab-side">
        {approvals.length > 0 && (
          <Panel
            icon={<Crown size={15} />}
            kicker="HUMAN APPROVAL"
            title={`人工审批 · ${approvals.length} 项待处理`}
            className="queen-panel"
          >
            <div className="queen-list">
              {approvals.map((approval) => (
                <article
                  key={approval.id}
                  className="queen-card"
                  onClick={() => setSelected(approval.task_id)}
                >
                  <strong>{approval.task_title ?? approval.task_id}</strong>
                  <span>
                    节点「{approval.stage_name ?? approval.stage_index + 1}」· 由{" "}
                    {nameOf(approval.requested_by)} 提交
                  </span>
                  {me.role === "admin" && (
                    <div className="queen-actions">
                      <button
                        className="rt-button rt-button--primary"
                        disabled={deciding !== null}
                        onClick={(e) => {
                          e.stopPropagation();
                          void decide(approval.id, "approve", "");
                        }}
                      >
                        批准
                      </button>
                      <button
                        disabled={deciding !== null}
                        onClick={(e) => {
                          e.stopPropagation();
                          const reason = window.prompt("驳回原因:");
                          if (!reason) return;
                          void decide(approval.id, "reject", reason);
                        }}
                      >
                        驳回
                      </button>
                    </div>
                  )}
                </article>
              ))}
            </div>
          </Panel>
        )}
        <Panel
          icon={<Megaphone size={15} />}
          kicker="DISPATCH HALL"
          title={`发单大厅 · ${openTasks.length} 单待接`}
        >
          <div className="hall-list">
            {openTasks.map((task) => (
              <article key={task.id} className="hall-card" onClick={() => setSelected(task.id)}>
                <header>
                  <strong>{task.title}</strong>
                  <span className="hall-wait">已等 {elapsedText(postedAt(task))}</span>
                </header>
                <div className="hall-meta">
                  <span className="holder-line">
                    <Avatar name={nameOf(task.created_by)} size={17} square />
                    {nameOf(task.created_by)} 发单
                  </span>
                  {task.priority !== "none" && (
                    <span className={`chip chip-${task.priority}`}>{PRIORITY_LABEL[task.priority]}</span>
                  )}
                  {task.acceptance.length > 0 && (
                    <span className="chip chip-dept">验收 {task.acceptance.length} 条</span>
                  )}
                </div>
                {me.actor_id && me.actor_id !== task.holder && (
                  <button
                    className="rt-button rt-button--primary hall-claim"
                    onClick={(e) => {
                      e.stopPropagation();
                      void claim(task);
                    }}
                  >
                    <Handshake size={13} /> 接单
                  </button>
                )}
              </article>
            ))}
            {openTasks.length === 0 && <p className="muted">大厅空闲,没有待接的单。</p>}
          </div>
        </Panel>
        </div>
      </div>

      <Panel icon={<Rows3 size={15} />} kicker="BOT LANES" title="BOT 泳道 · 各接单方在手工作">
        <div className="lane-table">
          <div className="lane-head">
            <span>接单方</span>
            <span>已接待开工</span>
            <span>执行中</span>
            <span>交付审校</span>
            <span>近期完成</span>
          </div>
          {agents.map((agent) => {
            const lane = laneOf(agent.id);
            const cell = (filter: (t: Task) => boolean) =>
              lane.filter(filter).map((t) => (
                <button
                  key={t.id}
                  className={`lane-chip ${t.status === "blocked" ? "is-blocked" : ""}`}
                  title={`${t.id} ${t.title}`}
                  onClick={() => setSelected(t.id)}
                >
                  {t.title.length > 11 ? t.title.slice(0, 10) + "…" : t.title}
                  {t.status === "doing" && <i style={{ width: `${t.progress}%` }} />}
                </button>
              ));
            return (
              <div key={agent.id} className="lane-row">
                <span className="lane-agent">
                  <Avatar name={agent.display_name || agent.id} size={24} square />
                  <span>
                    {agent.display_name || agent.id}
                    <small className={agent.online ? "is-online" : ""}>
                      {agent.online ? "在线" : "离线"} · {lane.filter((t) => t.status !== "done").length} 单在手
                    </small>
                  </span>
                </span>
                <span>{cell((t) => t.status === "queued" && !t.open_dispatch)}</span>
                <span>{cell((t) => t.status === "doing" || t.status === "blocked")}</span>
                <span>{cell((t) => t.status === "handoff")}</span>
                <span>{cell((t) => t.status === "done")}</span>
              </div>
            );
          })}
        </div>
      </Panel>

      {selected && (
        <TaskDrawer
          taskId={selected}
          me={me}
          actors={actors}
          onClose={() => setSelected(null)}
          onChanged={() => void load()}
        />
      )}
    </div>
  );
}
