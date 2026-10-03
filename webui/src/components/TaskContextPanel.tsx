import { useCallback, useEffect, useId, useRef, useState } from "react";
import { api, ApiError } from "../api";
import type { ActorInfo } from "../types";
import { STATUS_LABEL } from "../types";
import { collaborationTime, runIdentity, WAIT_KIND, needsProgressUpdate, type TaskContextSnapshot } from "../lib/collaboration";
import { ClaimList } from "./RunProgress";

interface Props {
  taskId: string;
  actors: ActorInfo[];
  revision: string;
}

/** A read-only handoff packet. It does not grant the proposed next actor authority. */
export default function TaskContextPanel({ taskId, actors, revision }: Props) {
  const [open, setOpen] = useState(false);
  const [packet, setPacket] = useState<TaskContextSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const requestSeq = useRef(0);
  const taskRef = useRef(taskId);
  taskRef.current = taskId;
  const regionId = useId();
  const nameOf = (id: string) => actors.find((actor) => actor.id === id)?.display_name || id;
  const taskName = (id: string) => packet?.collaboration.tasks.find((task) => task.id === id)?.title || id;
  const current = packet?.task_id === taskId ? packet : null;
  const delegation = current?.collaboration.delegations.find((item) => item.child_task_id === taskId);

  const load = useCallback(async () => {
    const seq = ++requestSeq.current;
    setLoading(true);
    setError("");
    try {
      const next = await api.get<TaskContextSnapshot>("/api/tasks/" + encodeURIComponent(taskId) + "/context");
      if (seq !== requestSeq.current || taskRef.current !== taskId) return;
      if (next.version !== 1 || next.task_id !== taskId || !Array.isArray(next.evidence)
        || !Array.isArray(next.next) || !Array.isArray(next.unverified_claims) || !Array.isArray(next.confirmed_progress)) {
        throw new Error("接棒信息格式不可用");
      }
      setPacket(next);
    } catch (cause) {
      if (seq !== requestSeq.current || taskRef.current !== taskId) return;
      setError(cause instanceof ApiError && [404, 405].includes(cause.status)
        ? "当前服务尚未提供接棒信息。"
        : cause instanceof ApiError && cause.status === 401
          ? "会话已过期，请重新登录后读取。"
          : "接棒信息读取失败，请重试；如已有内容，下方保留上次读取的版本。");
    } finally {
      if (seq === requestSeq.current && taskRef.current === taskId) setLoading(false);
    }
  }, [taskId]);

  useEffect(() => {
    if (open) void load();
    return () => { ++requestSeq.current; };
  }, [open, load, revision]);

  return <section className="task-collaboration__context">
    <header>
      <button type="button" aria-expanded={open} aria-controls={regionId} onClick={() => setOpen((value) => !value)}>
        接棒信息 <span aria-hidden="true">{open ? "−" : "+"}</span>
      </button>
      <span>任务要求、已有成果与下一步</span>
    </header>
    {open && <div id={regionId} aria-label="接棒信息内容">
      <div className="task-collaboration__context-toolbar">
        <p role="status">{loading ? "正在读取接棒信息…" : current ? "读取于 " + collaborationTime(current.generated_at) : "尚未取得接棒信息"}</p>
        <button type="button" onClick={() => void load()} disabled={loading}>刷新接棒信息</button>
      </div>
      {error && <p role="alert" className="task-collaboration__warning">{error}</p>}
      {current && <>
        <div className="task-collaboration__context-goal">
          <h5>{current.task.title}</h5>
          <p>当前持棒：{nameOf(current.task.holder)} · {STATUS_LABEL[current.task.status]}</p>
          {delegation && <p>{delegation.instruction}</p>}
          {current.task.acceptance.length > 0 && <><h5>验收要求</h5><ul>{current.task.acceptance.map((item, index) => <li key={index}>{item}</li>)}</ul></>}
        </div>
        <section aria-label="任务完成记录">
          <h5>已记录完成的任务</h5>
          <p className="task-collaboration__hint">以下仅表示任务卡已标记完成，成果内容仍需验收。</p>
          {current.confirmed_progress.length ? <ul>{current.confirmed_progress.map((item) => <li key={item.task_id}>
            <strong>{taskName(item.task_id)}</strong><small>{nameOf(item.who)} · {collaborationTime(item.at)}</small>
          </li>)}</ul> : <p className="task-collaboration__hint">尚无任务完成记录。</p>}
        </section>
        <section aria-label="待验收的执行者声明">
          <h5>待验收的执行者声明</h5>
          {current.unverified_claims.length ? current.unverified_claims.map((claim) => {
            const run = current.runs.find((item) => item.id === claim.run_id);
            return <article key={claim.run_id} className="task-collaboration__context-item">
              <strong>{taskName(claim.task_id)}</strong>
              <small>{run ? runIdentity(run) : nameOf(claim.actor_id)} · {collaborationTime(claim.reported_at)}</small>
              {claim.completed.length ? <ClaimList items={claim.completed} /> : <p>尚未列出完成项。</p>}
              {claim.progress && <p>上报计数：{claim.progress.completed}/{claim.progress.total} {claim.progress.unit}</p>}
              {run && needsProgressUpdate(run) && <span className="task-collaboration__tag" data-tone="warning">进展待更新</span>}
            </article>;
          }) : <p className="task-collaboration__hint">尚无结构化进展声明。</p>}
        </section>
        <section aria-label="接棒成果引用">
          <h5>成果引用 · 待核验</h5>
          {current.evidence.length ? current.evidence.map((item, index) => <article className="task-collaboration__context-item" key={index}>
            <strong>{item.summary}</strong>
            <small>{taskName(item.task_id)} · {item.source === "executor_report" ? "执行者上报" : "任务记录"}</small>
            <ul>{item.refs.map((ref, refIndex) => <li key={refIndex}>{ref}</li>)}</ul>
            {item.revision && <p>版本 / 修订：{item.revision}</p>}
          </article>) : <p className="task-collaboration__hint">尚未提供成果引用。</p>}
        </section>
        <section aria-label="各分支下一步">
          <h5>各分支下一步</h5>
          <p className="task-collaboration__hint">下一步来自执行者建议，不改变持棒或授权范围。</p>
          {current.next.length ? current.next.map((item) => <article className="task-collaboration__next" key={item.run_id}>
            <strong>{taskName(item.task_id)} · 下一棒 {item.owner ? nameOf(item.owner) : "待明确"}</strong>
            <p>{item.action || "下一步动作待补充"}</p>
            {item.remaining.length > 0 && <ul>{item.remaining.map((text, index) => <li key={index}>{text}</li>)}</ul>}
            {item.waiting && <p>{WAIT_KIND[item.waiting.kind]} · 责任人 {nameOf(item.waiting.owner)}：{item.waiting.reason}</p>}
          </article>) : <p className="task-collaboration__hint">尚未上报下一步。</p>}
        </section>
        <details className="task-collaboration__events">
          <summary>接棒包版本与范围</summary>
          <p>版本 {current.revision.content_hash.slice(0, 12)} · 任务更新于 {collaborationTime(current.revision.task_updated_at)}</p>
          <p>覆盖 {current.collaboration.coverage.instrumented_tasks}/{current.collaboration.coverage.total_tasks} 个任务的执行记录。</p>
          {current.privacy.contains_private_conversations === false && <p>共享包不包含私有会话正文。</p>}
        </details>
      </>}
    </div>}
  </section>;
}
