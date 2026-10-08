import { useI18n, t } from "../i18n";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { GitBranch, ListChecks, RefreshCw, GanttChart, Layers } from "lucide-react";
import { api, ApiError } from "../api";
import type { ActorInfo, Me } from "../types";
import { STATUS_LABEL } from "../types";
import { COLLABORATION_REFRESH_MS, startVisiblePolling } from "../lib/refresh";
import {
  collaborationTime, countedProgress, WAIT_KIND, runIdentity, runStatus, missingIdentity, identitySource, reporterLabel, needsProgressUpdate,
} from "../lib/collaboration";
import "./task-collaboration.css";
import TaskConversations from "./TaskConversations";
import DelegateTaskForm from "./DelegateTaskForm";
import RunProgress from "./RunProgress";
import TaskContextPanel from "./TaskContextPanel";
import DelegationPolicyForm from "./DelegationPolicyForm";
import { ModuleContributions, TaskRelationshipGraph, WorkerTimeline } from "./TaskCollaborationVisual";
import type { VisualSnapshot } from "../lib/collaborationVisual";

interface Props {
  taskId: string;
  actors: ActorInfo[];
  me: Me;
  onChanged?: () => void;
  onOpenTask?: (taskId: string) => void;
  layout?: "compact" | "wide";
}

/** Displays only explicit delegation and execution records, never inferred chat. */
export default function TaskCollaboration({ taskId, actors, me, onChanged, onOpenTask, layout = "compact" }: Props) {
  useI18n();
  const [snapshot, setSnapshot] = useState<VisualSnapshot | null>(null);
  const [error, setError] = useState("");
  const [unsupported, setUnsupported] = useState(false);
  const [loading, setLoading] = useState(true);
  const [view, setView] = useState<"delegations" | "timeline" | "modules" | "runs" | "conversations">("delegations");
  const [selection, setSelection] = useState("");
  const [retryNote, setRetryNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const requestSequence = useRef(0);
  const taskIdRef = useRef(taskId);
  taskIdRef.current = taskId;
  const headingId = useId();
  const noteId = useId();
  const nameOf = (id: string) => actors.find((actor) => actor.id === id)?.display_name || id || t("未记录责任人");

  const load = useCallback(async () => {
    const seq = ++requestSequence.current;
    setLoading(true);
    try {
      const next = await api.get<VisualSnapshot>(`/api/tasks/${encodeURIComponent(taskId)}/collaboration`);
      if (seq !== requestSequence.current || taskIdRef.current !== taskId) return;
      // An older server (or reverse proxy) may return a task-shaped response.
      if (!Array.isArray(next.runs) || !Array.isArray(next.delegations) || !Array.isArray(next.tasks)) {
        throw new Error(t("协作记录格式暂不可用"));
      }
      setSnapshot(next);
      setUnsupported(false);
      setError("");
    } catch (cause) {
      if (seq !== requestSequence.current || taskIdRef.current !== taskId) return;
      setUnsupported(cause instanceof ApiError && [404, 405].includes(cause.status));
      setError(cause instanceof ApiError && cause.status === 401
        ? t("会话已过期，请重新登录后刷新。")
        : t("同步失败，已保留上次读取的协作记录。请稍后重试。"));
    } finally {
      if (seq === requestSequence.current && taskIdRef.current === taskId) setLoading(false);
    }
  }, [taskId]);

  useEffect(() => {
    setSnapshot(null);
    setSelection("");
    setError("");
    setUnsupported(false);
    setNotice("");
    setRetryNote("");
    setBusy(false);
    const stop = startVisiblePolling(load, COLLABORATION_REFRESH_MS);
    return () => {
      ++requestSequence.current;
      stop();
    };
  }, [load]);

  const data = snapshot?.task_id === taskId ? snapshot : null;
  const taskSelection = selection.startsWith("task:") ? selection.slice(5) : taskId;
  const taskRuns = data?.runs.filter((run) => run.task_id === taskSelection) ?? [];
  // Task selection follows its newest execution; an explicit run selection is historical evidence.
  const selectedRun = selection.startsWith("run:")
    ? data?.runs.find((run) => `run:${run.id}` === selection)
    : taskRuns[taskRuns.length - 1]
      ?? (!selection && taskId === data?.root_task_id ? data?.runs[data.runs.length - 1] : undefined);
  const selectedTaskId = selectedRun?.task_id
    ?? (selection.startsWith("task:") ? selection.slice(5) : data?.tasks.some((task) => task.id === taskId) ? taskId : data?.root_task_id);
  const selectedTask = data?.tasks.find((task) => task.id === selectedTaskId);
  const delegation = data?.delegations.find((item) => item.child_task_id === selectedTaskId);
  const recovery = data?.recovery.find((item) => item.task_id === selectedTaskId);
  // Sequence is authoritative within a task; timestamps can arrive out of order.
  const events = data?.events.filter((event) => event.task_id === selectedTaskId)
    .slice().sort((a, b) => a.seq - b.seq) ?? [];
  const runRefs = [...new Set([...(selectedRun?.refs ?? []),
    ...(selectedRun?.progress_report?.completed.flatMap((claim) => claim.refs) ?? [])])];
  const refs = runRefs.length ? runRefs : [...new Set(selectedTask?.refs ?? [])];

  function chooseTask(id: string) {
    setSelection(`task:${id}`);
    setRetryNote("");
    setNotice("");
  }

  async function prepareRetry() {
    if (!recovery?.available || busy || !retryNote.trim()) return;
    const currentTaskId = taskId;
    setBusy(true);
    setNotice("");
    try {
      await api.post(`/api/tasks/${encodeURIComponent(recovery.task_id)}/collaboration/retry`, { note: retryNote.trim() });
      if (taskIdRef.current !== currentTaskId) return;
      setNotice(t("已准备重试此分支；等待执行器重新接单，尚未开始执行。"));
      setRetryNote("");
      await load();
      onChanged?.();
    } catch (cause) {
      if (taskIdRef.current === currentTaskId) {
        setNotice(cause instanceof ApiError ? t("重试准备失败：{v0}", { v0: cause.message }) : t("重试准备失败，请刷新后重试。"));
      }
    } finally {
      if (taskIdRef.current === currentTaskId) setBusy(false);
    }
  }

  return (
    <section className={`task-collaboration task-collaboration--${layout}`} aria-labelledby={headingId}>
      <header className="task-collaboration__head">
        <div>
          <span className="task-collaboration__eyebrow">COLLABORATION LIVE</span>
          <h3 id={headingId}><GitBranch size={17} aria-hidden="true" /> {t("协作现场")}</h3>
          <p>{t("谁委派了什么，谁在执行，下一步等谁。")}</p>
        </div>
        <button type="button" onClick={() => void load()} disabled={loading} aria-label={t("刷新协作记录")}>
          <RefreshCw size={14} aria-hidden="true" /> {t("刷新")} </button>
      </header>

      <div className="task-collaboration__sync" role="status">
        {loading ? (data ? t("正在同步最新记录…") : t("正在读取协作记录…"))
          : data ? t("最近同步 {v0} · 页面可见时自动更新", { v0: collaborationTime(data.generated_at) }) : t("尚未取得协作记录")}
      </div>
      {error && !unsupported && <p className="task-collaboration__warning" role="alert">{data ? error : t("协作记录加载失败，请刷新重试。")}</p>}
      {!data && !loading && unsupported && (
        <div className="task-collaboration__empty">
          <GitBranch size={23} aria-hidden="true" />
          <strong>{t("协作记录尚未接入")}</strong>
          <p>{t("当前服务尚未提供结构化委派与执行记录。原有任务事件和执行时间线仍可查看。")}</p>
        </div>
      )}

      {data && <>
        <div className="task-collaboration__summary" aria-label={t("已记录的协作概况")}>
          <span><strong>{data.delegations.length}</strong> {t("次委派")}</span>
          <span><strong>{data.runs.filter((run) => run.status === "running" && run.execution_state !== "prepared" && run.lease_current !== false && run.lease_live !== false).length}</strong> {t("执行中")}</span>
          <span><strong>{data.runs.filter((run) => run.status === "waiting" && run.execution_state !== "prepared" && run.lease_current !== false && run.lease_live !== false).length}</strong> {t("等待中")}</span>
          <span>{t("执行记录覆盖")} <strong>{data.coverage.instrumented_tasks}/{data.coverage.total_tasks}</strong> {t("个任务")}</span>
        </div>

        {!me.readonly && <DelegationPolicyForm key={taskId} snapshot={data} actors={actors} onChanged={() => void load()} />}
        {data.can_delegate && !me.readonly && <DelegateTaskForm key={taskId} taskId={taskId} leaseTerm={data.delegation_lease_term ?? null} actors={actors} targets={data.delegation_targets ?? []} onCreated={() => { void load(); onChanged?.(); }} />}

        {data.runs.length === 0 && data.delegations.length === 0 && <p className="collab-visual__coverage"><strong>{t("尚未记录结构化协作")}</strong> {t("· 已展示任务卡状态与历史。聊天分工不自动变成确认的委派。")}</p>}
        <>
          <div className="task-collaboration__views" role="group" aria-label={t("协作视图")}>
            <button type="button" aria-pressed={view === "delegations"} onClick={() => setView("delegations")}><GitBranch size={14} aria-hidden="true" /> {t("任务关系图")}</button>
            <button type="button" aria-pressed={view === "timeline"} onClick={() => setView("timeline")}><GanttChart size={14} aria-hidden="true" /> {t("设备模型泳道")}</button>
            <button type="button" aria-pressed={view === "modules"} onClick={() => setView("modules")}><Layers size={14} aria-hidden="true" /> {t("模块贡献")}</button>
            {me.task_conversations && <button type="button" aria-pressed={view === "conversations"} onClick={() => setView("conversations")}>{t("沟通记录")}</button>}
            <button type="button" aria-pressed={view === "runs"} onClick={() => setView("runs")}><ListChecks size={14} aria-hidden="true" /> {t("执行进展")}</button>
          </div>
          <div className="collab-visual__stage">
          {view === "conversations" ? <TaskConversations key={taskId} taskId={taskId} /> : view === "delegations" ? (
            <TaskRelationshipGraph snapshot={data} selectedTaskId={selectedTaskId} selectedRunId={selectedRun?.id} actors={actors} disabled={busy} onSelectTask={chooseTask} onSelectRun={(id) => setSelection(`run:${id}`)} onOpenTask={onOpenTask} />
          ) : view === "timeline" ? (
            <WorkerTimeline snapshot={data} selectedTaskId={selectedTaskId} selectedRunId={selectedRun?.id} actors={actors} disabled={busy} onSelectTask={chooseTask} onSelectRun={(id) => { setSelection(`run:${id}`); setRetryNote(""); setNotice(""); }} />
          ) : view === "modules" ? (
            <ModuleContributions snapshot={data} selectedTaskId={selectedTaskId} actors={actors} disabled={busy} onSelectTask={chooseTask} onSelectRun={(id) => { setSelection(`run:${id}`); setRetryNote(""); setNotice(""); }} />
          ) : (
            <div className="task-collaboration__runs">
              {data.runs.length === 0 && <p className="task-collaboration__hint">{t("委派已经记录，执行器尚未上报执行记录。")}</p>}
              {data.runs.map((run) => (
                <button type="button" disabled={busy} key={run.id} className="task-collaboration__run" aria-pressed={selectedRun?.id === run.id} aria-label={runIdentity(run) + " · " + nameOf(run.actor_id) + " · " + runStatus(run) + " · " + run.title}
                  onClick={() => { setSelection(`run:${run.id}`); setRetryNote(""); setNotice(""); }}>
                  <span className="task-collaboration__run-head"><strong>{runIdentity(run)}</strong><em data-status={run.execution_state === "prepared" ? "queued" : run.status}>{runStatus(run)}</em></span>
                  <small>{t("现有身份")} {nameOf(run.actor_id)} · {run.runtime || t("运行端未知")}</small>
                  {(run.identity_complete === false || missingIdentity(run).length > 0) && <span className="task-collaboration__tag" data-tone="warning">{t("资料不完整")}</span>}
                  <span>{run.title}</span>
                  <small>{countedProgress(run)}</small>
                  <small>{t("最近上报")} {collaborationTime(run.last_report_at || run.updated_at)}{needsProgressUpdate(run) ? t(" · 进展待更新") : ""}</small>
                  {run.waiting && <span className="task-collaboration__waiting-line">{t(WAIT_KIND[run.waiting.kind])} {t("· 责任人")} {nameOf(run.waiting.owner)}</span>}
                </button>
              ))}
            </div>
          )}

          {view !== "conversations" && (selectedTask || selectedRun) && <article className="task-collaboration__detail" aria-label={t("选中分支的执行证据")}>
            <header><div><small>{t("选中分支")}</small><h4>{selectedRun?.title || selectedTask?.title}</h4></div>
              {onOpenTask && selectedTaskId && selectedTaskId !== taskId && <button type="button" onClick={() => onOpenTask(selectedTaskId)}>{t("查看任务")}</button>}
            </header>
            {selectedRun ? <>
              <div className="task-collaboration__identity" aria-label={t("执行身份资料")}>
                <strong>{runIdentity(selectedRun)}</strong>
                {(selectedRun.identity_complete === false || missingIdentity(selectedRun).length > 0) && <span className="task-collaboration__tag" data-tone="warning">{t("资料不完整")}</span>}
                {missingIdentity(selectedRun).length > 0 && <p>{t("尚缺：")}{missingIdentity(selectedRun).join(t("、"))}{t("。保留为未知，不使用当前名册补写历史。")}</p>}
              </div>
              <dl className="task-collaboration__facts">
                <div><dt>{t("现有身份")}</dt><dd>{nameOf(selectedRun.actor_id)}</dd></div>
                <div><dt>{t("设备")}</dt><dd>{selectedRun.node || t("设备未知")}<small>{identitySource(selectedRun.node_source)}</small></dd></div>
                <div><dt>{t("本次模型")}</dt><dd>{selectedRun.model || t("模型未知")}<small>{identitySource(selectedRun.model_source)}</small></dd></div>
                <div><dt>{t("运行端")}</dt><dd>{selectedRun.runtime || t("运行端未知")}<small>{identitySource(selectedRun.runtime_source)}</small></dd></div>
                <div><dt>{t("身份记录者")}</dt><dd>{reporterLabel(selectedRun.reported_by)}</dd></div>
                <div><dt>{t("身份记录时间")}</dt><dd>{collaborationTime(selectedRun.identity_recorded_at)}</dd></div>
                <div><dt>{t("执行状态")}</dt><dd>{runStatus(selectedRun)}</dd></div>
                <div><dt>{t("上报进度")}</dt><dd>{countedProgress(selectedRun)}</dd></div>
                <div><dt>{t("最近报告者")}</dt><dd>{reporterLabel(selectedRun.last_reported_by)}</dd></div>
                <div><dt>{t("最近上报")}</dt><dd>{collaborationTime(selectedRun.last_report_at || selectedRun.updated_at)}</dd></div>
                <div><dt>{t("最近心跳")}</dt><dd>{collaborationTime(selectedRun.heartbeat_at)}</dd></div>
                {selectedRun.prepared_at && <div><dt>{t("准备时间")}</dt><dd>{collaborationTime(selectedRun.prepared_at)}</dd></div>}
                <div><dt>{t("开始时间")}</dt><dd>{collaborationTime(selectedRun.started_at)}</dd></div>
                <div><dt>{t("结束时间")}</dt><dd>{collaborationTime(selectedRun.ended_at)}</dd></div>
              </dl>
              <p className="task-collaboration__hint">{t("设备、模型和运行端取自本次执行记录；登记值与上报值均未独立核验。")}</p>
              <RunProgress run={selectedRun} nameOf={nameOf} />
              <details className="task-collaboration__events">
                <summary>{t("执行记录详情")}</summary>
                <dl className="task-collaboration__facts">
                  <div><dt>{t("记录编号")}</dt><dd>{selectedRun.id}</dd></div>
                  <div><dt>{t("关联尝试")}</dt><dd>{selectedRun.attempt_id || t("尚未关联")}</dd></div>
                </dl>
              </details>
              {(selectedRun.lease_current === false || selectedRun.lease_live === false) && ["running", "waiting"].includes(selectedRun.status) && <p className="task-collaboration__warning">{t("租约已过期或已更换，当前执行状态待核实；以下为最后一次上报。")}</p>}
              {selectedRun.waiting && <div className="task-collaboration__waiting">
                <strong>{t(WAIT_KIND[selectedRun.waiting.kind])} {t("· 责任人")} {nameOf(selectedRun.waiting.owner)}</strong>
                <p>{selectedRun.waiting.reason}</p>
                <small>{t("自")} {collaborationTime(selectedRun.waiting.since)} {t("起等待")}</small>
              </div>}
              <div className="task-collaboration__evidence"><h5>{t("最新执行回执")}</h5><p>{selectedRun.latest_note || t("执行器尚未上报回执。")}</p></div>
            </> : <p className="task-collaboration__hint">{t("该分支尚无执行上报；任务状态为「{status}」。", { status: selectedTask ? t(STATUS_LABEL[selectedTask.status]) : t("未知") })}</p>}
            {delegation && <div className="task-collaboration__evidence"><h5>{t("委派要求")}</h5><p>{delegation.instruction || t("未记录具体要求")}</p><small>{nameOf(delegation.delegated_by)} {t("委派给")} {nameOf(delegation.delegated_to)} · {collaborationTime(delegation.created_at)}</small></div>}
            {(delegation?.acceptance ?? selectedTask?.acceptance ?? []).length > 0 && <div className="task-collaboration__evidence"><h5>{t("验收要求")}</h5><ul>{(delegation?.acceptance ?? selectedTask?.acceptance ?? []).map((item, index) => <li key={index}>{item}</li>)}</ul></div>}
            <div className="task-collaboration__evidence"><h5>{t("成果引用")}</h5>{refs.length ? <ul>{refs.map((ref, index) => <li key={`${ref}-${index}`}>{ref}</li>)}</ul> : <p>{t("尚未上报成果引用。")}</p>}</div>
            {events.length > 0 && <details className="task-collaboration__events"><summary>{t("查看分支事件证据（{count}）", { count: events.length })}</summary><ol>{events.map((event) => <li key={event.id}><header><strong>#{event.seq} · {nameOf(event.who)}</strong><time>{collaborationTime(event.at)}</time></header><p>{event.did}</p></li>)}</ol></details>}
            {selectedTaskId && <TaskContextPanel key={selectedTaskId} taskId={selectedTaskId} actors={actors} revision={String(data.cursor.event_id) + ":" + String(data.cursor.attempt_id)} />}
            {recovery && <div className="task-collaboration__recovery">
              <h5>{t("分支恢复")}</h5>
              {recovery.available && !me.readonly ? <>
                <p>{t("只为当前分支准备重试，保留其他分支的成果；随后等待执行器重新接单。")}</p>
                <label htmlFor={noteId}>{t("重试原因")}</label>
                <textarea id={noteId} value={retryNote} onChange={(event) => setRetryNote(event.target.value)} maxLength={240} rows={2} placeholder={t("说明已解决的阻碍或调整要求")} disabled={busy} />
                <button type="button" disabled={busy || !retryNote.trim()} onClick={() => void prepareRetry()}>{busy ? t("正在准备重试…") : t("准备重试此分支")}</button>
              </> : <p>{me.readonly ? t("当前为只读模式。") : recovery.reason || t("该分支当前无需恢复。")}</p>}
            </div>}
            {notice && <p className="task-collaboration__notice" role="status">{notice}</p>}
          </article>}
          </div>
        </>
      </>}
    </section>
  );
}
