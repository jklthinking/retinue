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
  const [snapshot, setSnapshot] = useState<VisualSnapshot | null>(null);
  const [error, setError] = useState("");
  const [unsupported, setUnsupported] = useState(false);
  const [loading, setLoading] = useState(true);
  const [view, setView] = useState<"delegations" | "timeline" | "modules" | "runs">("delegations");
  const [selection, setSelection] = useState("");
  const [retryNote, setRetryNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const requestSequence = useRef(0);
  const taskIdRef = useRef(taskId);
  taskIdRef.current = taskId;
  const headingId = useId();
  const noteId = useId();
  const nameOf = (id: string) => actors.find((actor) => actor.id === id)?.display_name || id || "未记录责任人";

  const load = useCallback(async () => {
    const seq = ++requestSequence.current;
    setLoading(true);
    try {
      const next = await api.get<VisualSnapshot>(`/api/tasks/${encodeURIComponent(taskId)}/collaboration`);
      if (seq !== requestSequence.current || taskIdRef.current !== taskId) return;
      // An older server (or reverse proxy) may return a task-shaped response.
      if (!Array.isArray(next.runs) || !Array.isArray(next.delegations) || !Array.isArray(next.tasks)) {
        throw new Error("协作记录格式暂不可用");
      }
      setSnapshot(next);
      setUnsupported(false);
      setError("");
    } catch (cause) {
      if (seq !== requestSequence.current || taskIdRef.current !== taskId) return;
      setUnsupported(cause instanceof ApiError && [404, 405].includes(cause.status));
      setError(cause instanceof ApiError && cause.status === 401
        ? "会话已过期，请重新登录后刷新。"
        : "同步失败，已保留上次读取的协作记录。请稍后重试。");
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
      setNotice("已准备重试此分支；等待执行器重新接单，尚未开始执行。");
      setRetryNote("");
      await load();
      onChanged?.();
    } catch (cause) {
      if (taskIdRef.current === currentTaskId) {
        setNotice(cause instanceof ApiError ? `重试准备失败：${cause.message}` : "重试准备失败，请刷新后重试。");
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
          <h3 id={headingId}><GitBranch size={17} aria-hidden="true" /> 协作现场</h3>
          <p>谁委派了什么，谁在执行，下一步等谁。</p>
        </div>
        <button type="button" onClick={() => void load()} disabled={loading} aria-label="刷新协作记录">
          <RefreshCw size={14} aria-hidden="true" /> 刷新
        </button>
      </header>

      <div className="task-collaboration__sync" role="status">
        {loading ? (data ? "正在同步最新记录…" : "正在读取协作记录…")
          : data ? `最近同步 ${collaborationTime(data.generated_at)} · 页面可见时自动更新` : "尚未取得协作记录"}
      </div>
      {error && !unsupported && <p className="task-collaboration__warning" role="alert">{data ? error : "协作记录加载失败，请刷新重试。"}</p>}
      {!data && !loading && unsupported && (
        <div className="task-collaboration__empty">
          <GitBranch size={23} aria-hidden="true" />
          <strong>协作记录尚未接入</strong>
          <p>当前服务尚未提供结构化委派与执行记录。原有任务事件和执行时间线仍可查看。</p>
        </div>
      )}

      {data && <>
        <div className="task-collaboration__summary" aria-label="已记录的协作概况">
          <span><strong>{data.delegations.length}</strong> 次委派</span>
          <span><strong>{data.runs.filter((run) => run.status === "running" && run.execution_state !== "prepared" && run.lease_current !== false && run.lease_live !== false).length}</strong> 执行中</span>
          <span><strong>{data.runs.filter((run) => run.status === "waiting" && run.execution_state !== "prepared" && run.lease_current !== false && run.lease_live !== false).length}</strong> 等待中</span>
          <span>执行记录覆盖 <strong>{data.coverage.instrumented_tasks}/{data.coverage.total_tasks}</strong> 个任务</span>
        </div>

        {!me.readonly && <DelegationPolicyForm key={taskId} snapshot={data} actors={actors} onChanged={() => void load()} />}
        {data.can_delegate && !me.readonly && <DelegateTaskForm key={taskId} taskId={taskId} leaseTerm={data.delegation_lease_term ?? null} actors={actors} targets={data.delegation_targets ?? []} onCreated={() => { void load(); onChanged?.(); }} />}

        {data.runs.length === 0 && data.delegations.length === 0 && <p className="collab-visual__coverage"><strong>尚未记录结构化协作</strong> · 已展示任务卡状态与历史。聊天分工不自动变成确认的委派。</p>}
        <>
          <div className="task-collaboration__views" role="group" aria-label="协作视图">
            <button type="button" aria-pressed={view === "delegations"} onClick={() => setView("delegations")}><GitBranch size={14} aria-hidden="true" /> 任务关系图</button>
            <button type="button" aria-pressed={view === "timeline"} onClick={() => setView("timeline")}><GanttChart size={14} aria-hidden="true" /> 设备模型泳道</button>
            <button type="button" aria-pressed={view === "modules"} onClick={() => setView("modules")}><Layers size={14} aria-hidden="true" /> 模块贡献</button>
            <button type="button" aria-pressed={view === "runs"} onClick={() => setView("runs")}><ListChecks size={14} aria-hidden="true" /> 执行进展</button>
          </div>
          <div className="collab-visual__stage">
          {view === "delegations" ? (
            <TaskRelationshipGraph snapshot={data} selectedTaskId={selectedTaskId} selectedRunId={selectedRun?.id} actors={actors} disabled={busy} onSelectTask={chooseTask} onSelectRun={(id) => setSelection(`run:${id}`)} onOpenTask={onOpenTask} />
          ) : view === "timeline" ? (
            <WorkerTimeline snapshot={data} selectedTaskId={selectedTaskId} selectedRunId={selectedRun?.id} actors={actors} disabled={busy} onSelectTask={chooseTask} onSelectRun={(id) => { setSelection(`run:${id}`); setRetryNote(""); setNotice(""); }} />
          ) : view === "modules" ? (
            <ModuleContributions snapshot={data} selectedTaskId={selectedTaskId} actors={actors} disabled={busy} onSelectTask={chooseTask} onSelectRun={(id) => { setSelection(`run:${id}`); setRetryNote(""); setNotice(""); }} />
          ) : (
            <div className="task-collaboration__runs">
              {data.runs.length === 0 && <p className="task-collaboration__hint">委派已经记录，执行器尚未上报执行记录。</p>}
              {data.runs.map((run) => (
                <button type="button" disabled={busy} key={run.id} className="task-collaboration__run" aria-pressed={selectedRun?.id === run.id} aria-label={runIdentity(run) + " · " + nameOf(run.actor_id) + " · " + runStatus(run) + " · " + run.title}
                  onClick={() => { setSelection(`run:${run.id}`); setRetryNote(""); setNotice(""); }}>
                  <span className="task-collaboration__run-head"><strong>{runIdentity(run)}</strong><em data-status={run.execution_state === "prepared" ? "queued" : run.status}>{runStatus(run)}</em></span>
                  <small>现有身份 {nameOf(run.actor_id)} · {run.runtime || "运行端未知"}</small>
                  {(run.identity_complete === false || missingIdentity(run).length > 0) && <span className="task-collaboration__tag" data-tone="warning">资料不完整</span>}
                  <span>{run.title}</span>
                  <small>{countedProgress(run)}</small>
                  <small>最近上报 {collaborationTime(run.last_report_at || run.updated_at)}{needsProgressUpdate(run) ? " · 进展待更新" : ""}</small>
                  {run.waiting && <span className="task-collaboration__waiting-line">{WAIT_KIND[run.waiting.kind]} · 责任人 {nameOf(run.waiting.owner)}</span>}
                </button>
              ))}
            </div>
          )}

          {(selectedTask || selectedRun) && <article className="task-collaboration__detail" aria-label="选中分支的执行证据">
            <header><div><small>选中分支</small><h4>{selectedRun?.title || selectedTask?.title}</h4></div>
              {onOpenTask && selectedTaskId && selectedTaskId !== taskId && <button type="button" onClick={() => onOpenTask(selectedTaskId)}>查看任务</button>}
            </header>
            {selectedRun ? <>
              <div className="task-collaboration__identity" aria-label="执行身份资料">
                <strong>{runIdentity(selectedRun)}</strong>
                {(selectedRun.identity_complete === false || missingIdentity(selectedRun).length > 0) && <span className="task-collaboration__tag" data-tone="warning">资料不完整</span>}
                {missingIdentity(selectedRun).length > 0 && <p>尚缺：{missingIdentity(selectedRun).join("、")}。保留为未知，不使用当前名册补写历史。</p>}
              </div>
              <dl className="task-collaboration__facts">
                <div><dt>现有身份</dt><dd>{nameOf(selectedRun.actor_id)}</dd></div>
                <div><dt>设备</dt><dd>{selectedRun.node || "设备未知"}<small>{identitySource(selectedRun.node_source)}</small></dd></div>
                <div><dt>本次模型</dt><dd>{selectedRun.model || "模型未知"}<small>{identitySource(selectedRun.model_source)}</small></dd></div>
                <div><dt>运行端</dt><dd>{selectedRun.runtime || "运行端未知"}<small>{identitySource(selectedRun.runtime_source)}</small></dd></div>
                <div><dt>身份记录者</dt><dd>{reporterLabel(selectedRun.reported_by)}</dd></div>
                <div><dt>身份记录时间</dt><dd>{collaborationTime(selectedRun.identity_recorded_at)}</dd></div>
                <div><dt>执行状态</dt><dd>{runStatus(selectedRun)}</dd></div>
                <div><dt>上报进度</dt><dd>{countedProgress(selectedRun)}</dd></div>
                <div><dt>最近报告者</dt><dd>{reporterLabel(selectedRun.last_reported_by)}</dd></div>
                <div><dt>最近上报</dt><dd>{collaborationTime(selectedRun.last_report_at || selectedRun.updated_at)}</dd></div>
                <div><dt>最近心跳</dt><dd>{collaborationTime(selectedRun.heartbeat_at)}</dd></div>
                {selectedRun.prepared_at && <div><dt>准备时间</dt><dd>{collaborationTime(selectedRun.prepared_at)}</dd></div>}
                <div><dt>开始时间</dt><dd>{collaborationTime(selectedRun.started_at)}</dd></div>
                <div><dt>结束时间</dt><dd>{collaborationTime(selectedRun.ended_at)}</dd></div>
              </dl>
              <p className="task-collaboration__hint">设备、模型和运行端取自本次执行记录；登记值与上报值均未独立核验。</p>
              <RunProgress run={selectedRun} nameOf={nameOf} />
              <details className="task-collaboration__events">
                <summary>执行记录详情</summary>
                <dl className="task-collaboration__facts">
                  <div><dt>记录编号</dt><dd>{selectedRun.id}</dd></div>
                  <div><dt>关联尝试</dt><dd>{selectedRun.attempt_id || "尚未关联"}</dd></div>
                </dl>
              </details>
              {(selectedRun.lease_current === false || selectedRun.lease_live === false) && ["running", "waiting"].includes(selectedRun.status) && <p className="task-collaboration__warning">租约已过期或已更换，当前执行状态待核实；以下为最后一次上报。</p>}
              {selectedRun.waiting && <div className="task-collaboration__waiting">
                <strong>{WAIT_KIND[selectedRun.waiting.kind]} · 责任人 {nameOf(selectedRun.waiting.owner)}</strong>
                <p>{selectedRun.waiting.reason}</p>
                <small>自 {collaborationTime(selectedRun.waiting.since)} 起等待</small>
              </div>}
              <div className="task-collaboration__evidence"><h5>最新执行回执</h5><p>{selectedRun.latest_note || "执行器尚未上报回执。"}</p></div>
            </> : <p className="task-collaboration__hint">该分支尚无执行上报；任务状态为{selectedTask ? `「${STATUS_LABEL[selectedTask.status]}」` : "未知"}。</p>}
            {delegation && <div className="task-collaboration__evidence"><h5>委派要求</h5><p>{delegation.instruction || "未记录具体要求"}</p><small>{nameOf(delegation.delegated_by)} 委派给 {nameOf(delegation.delegated_to)} · {collaborationTime(delegation.created_at)}</small></div>}
            {(delegation?.acceptance ?? selectedTask?.acceptance ?? []).length > 0 && <div className="task-collaboration__evidence"><h5>验收要求</h5><ul>{(delegation?.acceptance ?? selectedTask?.acceptance ?? []).map((item, index) => <li key={index}>{item}</li>)}</ul></div>}
            <div className="task-collaboration__evidence"><h5>成果引用</h5>{refs.length ? <ul>{refs.map((ref, index) => <li key={`${ref}-${index}`}>{ref}</li>)}</ul> : <p>尚未上报成果引用。</p>}</div>
            {events.length > 0 && <details className="task-collaboration__events"><summary>查看分支事件证据（{events.length}）</summary><ol>{events.map((event) => <li key={event.id}><header><strong>#{event.seq} · {nameOf(event.who)}</strong><time>{collaborationTime(event.at)}</time></header><p>{event.did}</p></li>)}</ol></details>}
            {selectedTaskId && <TaskContextPanel key={selectedTaskId} taskId={selectedTaskId} actors={actors} revision={String(data.cursor.event_id) + ":" + String(data.cursor.attempt_id)} />}
            {recovery && <div className="task-collaboration__recovery">
              <h5>分支恢复</h5>
              {recovery.available && !me.readonly ? <>
                <p>只为当前分支准备重试，保留其他分支的成果；随后等待执行器重新接单。</p>
                <label htmlFor={noteId}>重试原因</label>
                <textarea id={noteId} value={retryNote} onChange={(event) => setRetryNote(event.target.value)} maxLength={240} rows={2} placeholder="说明已解决的阻碍或调整要求" disabled={busy} />
                <button type="button" disabled={busy || !retryNote.trim()} onClick={() => void prepareRetry()}>{busy ? "正在准备重试…" : "准备重试此分支"}</button>
              </> : <p>{me.readonly ? "当前为只读模式。" : recovery.reason || "该分支当前无需恢复。"}</p>}
            </div>}
            {notice && <p className="task-collaboration__notice" role="status">{notice}</p>}
          </article>}
          </div>
        </>
      </>}
    </section>
  );
}
