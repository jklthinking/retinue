import { useId, useRef, useState, type FormEvent } from "react";
import { api, ApiError } from "../api";
import type { ActorInfo } from "../types";
import { isSessionSync, registeredModel, rosterIdentity } from "../lib/rosterIdentity";

interface Props {
  taskId: string;
  leaseTerm: number | null;
  actors: ActorInfo[];
  targets: string[];
  onCreated: () => void;
}

/** The request key follows its intent, so retrying an uncertain write is safe. */
export default function DelegateTaskForm({ taskId, leaseTerm, actors, targets, onCreated }: Props) {
  const prefix = useId();
  const agents = actors.filter((actor) => actor.kind === "agent" && !actor.disabled && !isSessionSync(actor) && targets.includes(actor.id));
  const [assignee, setAssignee] = useState("");
  const [reviewer, setReviewer] = useState("");
  const [title, setTitle] = useState("");
  const [instruction, setInstruction] = useState("");
  const [module, setModule] = useState("");
  const [acceptance, setAcceptance] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const requestKey = useRef<{ fingerprint: string; key: string } | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    const criteria = acceptance.split("\n").map((line) => line.trim()).filter(Boolean);
    if (!agents.some((actor) => actor.id === assignee) || !title.trim() || !instruction.trim() || !criteria.length) {
      setMessage("请选择执行者，并填写标题、委派要求和至少一条验收标准。");
      return;
    }
    if (reviewer && (reviewer === assignee || !agents.some((actor) => actor.id === reviewer))) {
      setMessage("请选择授权范围内且不同于执行者的复核模型。");
      return;
    }
    if (criteria.length > 32 || criteria.some((line) => line.length > 240)) {
      setMessage("验收标准最多 32 条，每条最多 240 字。");
      return;
    }
    const intent = { delegated_to: assignee, title: title.trim(), instruction: instruction.trim(), acceptance: criteria,
      ...(module.trim() ? { module: module.trim() } : {}),
      ...(reviewer ? { pipeline: [
        { name: "执行", holder: assignee, gate: "auto" },
        { name: "复核", holder: reviewer, gate: "review" },
      ] } : {}),
    };
    const fingerprint = JSON.stringify({ taskId, ...intent });
    if (requestKey.current?.fingerprint !== fingerprint) {
      requestKey.current = {
        fingerprint,
        key: `web-${globalThis.crypto?.randomUUID?.() ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`}`,
      };
    }
    setBusy(true);
    setMessage("");
    try {
      await api.post(`/api/tasks/${encodeURIComponent(taskId)}/delegations`, {
        ...intent, idempotency_key: requestKey.current.key, lease_term: leaseTerm,
      });
      setMessage("已建立委派子卡，等待执行者开工。");
      setTitle("");
      setInstruction("");
      setAcceptance("");
      setModule("");
      setReviewer("");
      requestKey.current = null;
      onCreated();
    } catch (cause) {
      setMessage(cause instanceof ApiError ? `委派失败：${cause.message}` : "未能确认委派结果。可重试，系统会复用本次请求标识。");
    } finally {
      setBusy(false);
    }
  }

  return <details className="task-collaboration__delegate">
    <summary>委派子任务</summary>
    <form onSubmit={submit}>
      <p>把一个独立分支交给 Agent，记录要求与验收标准。建立子卡后等待执行者开工。</p>
      <p>按设备与登记模型选择现有成员；型号待确认的成员仍可选择，会话同步代理不参与派单。</p>
      <label htmlFor={`${prefix}-agent`}>执行者</label>
      <select id={`${prefix}-agent`} value={assignee} onChange={(event) => { setAssignee(event.target.value); if (event.target.value === reviewer) setReviewer(""); }} disabled={busy} required>
        <option value="">选择执行 Agent</option>
        {agents.map((agent) => <option value={agent.id} key={agent.id}>
          {rosterIdentity(agent)} · {agent.display_name || agent.id}{registeredModel(agent.model).state === "alias" ? "（配置别名，精确型号待确认）" : ""}
        </option>)}
      </select>
      <label htmlFor={`${prefix}-reviewer`}>交付后复核模型（可选）</label>
      <select id={`${prefix}-reviewer`} value={reviewer} onChange={(event) => setReviewer(event.target.value)} disabled={busy || !assignee}>
        <option value="">暂不设置复核</option>
        {assignee && agents.filter((agent) => agent.id !== assignee).map((agent) => <option value={agent.id} key={agent.id}>{rosterIdentity(agent)} · {agent.display_name || agent.id}</option>)}
      </select>
      <p>设置后先由执行者交付，再交给复核模型；退回与接棒保留在任务流中。</p>
      <label htmlFor={`${prefix}-title`}>子任务标题</label>
      <input id={`${prefix}-title`} value={title} onChange={(event) => setTitle(event.target.value)} maxLength={240} disabled={busy} required />
      <label htmlFor={`${prefix}-module`}>功能模块（可选）</label>
      <input id={`${prefix}-module`} value={module} onChange={(event) => setModule(event.target.value)} maxLength={80} disabled={busy} placeholder="例如：阅读器、词库、文档" />
      <p>明确登记这个分支负责的功能模块，留空时显示模块未标注。</p>
      <label htmlFor={`${prefix}-instruction`}>委派要求</label>
      <input id={`${prefix}-instruction`} value={instruction} onChange={(event) => setInstruction(event.target.value)} maxLength={240} disabled={busy} required placeholder="一句话说清执行者需要完成什么" />
      <label htmlFor={`${prefix}-acceptance`}>验收标准（每行一条）</label>
      <textarea id={`${prefix}-acceptance`} value={acceptance} onChange={(event) => setAcceptance(event.target.value)} rows={3} disabled={busy} required placeholder="例如：提供结论及可核查的来源" />
      <button type="submit" disabled={busy || !agents.length || Boolean(reviewer && (reviewer === assignee || !agents.some((actor) => actor.id === reviewer)))}>{busy ? "正在建立子卡…" : "建立委派子卡"}</button>
      {!agents.length && <p>名册中尚无可用的 Agent。</p>}
      {message && <p role="status">{message}</p>}
    </form>
  </details>;
}
