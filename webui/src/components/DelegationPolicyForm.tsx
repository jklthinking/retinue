import { useId, useRef, useState, type FormEvent } from "react";
import { api, ApiError } from "../api";
import type { ActorInfo } from "../types";
import type { CollaborationSnapshot } from "../lib/collaboration";
import { isSessionSync, registeredModel, rosterIdentity } from "../lib/rosterIdentity";

interface Props {
  snapshot: CollaborationSnapshot;
  actors: ActorInfo[];
  onChanged: () => void;
}

/** An operator grants a bounded scope; agents can consume but never extend it. */
export default function DelegationPolicyForm({ snapshot, actors, onChanged }: Props) {
  const prefix = useId();
  const [selected, setSelected] = useState<string[] | null>(null);
  const [depth, setDepth] = useState<number | null>(null);
  const [children, setChildren] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const requestKey = useRef<{ fingerprint: string; key: string } | null>(null);
  if (!snapshot.can_manage_delegation_policy) return null;
  const policy = snapshot.delegation_policy;
  const syncIds = new Set(actors.filter(isSessionSync).map((actor) => actor.id));
  const allowed = (selected ?? policy?.allowed_actor_ids ?? []).filter((id) => !syncIds.has(id));
  const policyHasSync = policy?.allowed_actor_ids.some((id) => syncIds.has(id));
  const maxDepth = depth ?? policy?.max_depth ?? 3;
  const maxChildren = children ?? policy?.max_children ?? 12;
  const agents = actors.filter((actor) => actor.kind === "agent" && !actor.disabled && !isSessionSync(actor));

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    const intent = { allowed_actor_ids: [...allowed].sort(), max_depth: maxDepth, max_children: maxChildren };
    const fingerprint = JSON.stringify({ task: snapshot.root_task_id, ...intent });
    if (requestKey.current?.fingerprint !== fingerprint) {
      requestKey.current = { fingerprint, key: `policy-${globalThis.crypto?.randomUUID?.() ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`}` };
    }
    setBusy(true);
    setMessage("");
    try {
      await api.post(`/api/tasks/${encodeURIComponent(snapshot.root_task_id)}/collaboration/policy`, {
        ...intent, idempotency_key: requestKey.current.key,
      });
      setMessage(allowed.length ? "已保存此任务的委派范围；名单内的 Agent 可按额度继续委派。" : "已关闭 Agent 跨成员委派，已有子任务保留。");
      requestKey.current = null;
      onChanged();
    } catch (cause) {
      setMessage(cause instanceof ApiError ? `保存失败：${cause.message}` : "未能确认保存结果。重试会复用本次请求标识。");
    } finally {
      setBusy(false);
    }
  }

  return <details className="task-collaboration__delegate">
    <summary>Agent 委派权限 · {policy?.allowed_actor_ids.length ? "已限定范围" : "未开启跨成员委派"}</summary>
    <form onSubmit={submit}>
      <p>只对当前任务生效。勾选的 Agent 可相互委派；未勾选的 Agent 仍只能拆分给自己。清空名单并保存可撤销后续跨成员委派。</p>
      <p>参与者按设备与登记模型展示，型号待确认的真实成员仍可勾选。</p>
      {policyHasSync && <p>原名单含会话同步代理；保存后将移除这些代理，已有子任务保留。</p>}
      <fieldset disabled={busy} style={{ border: "1px solid var(--line)", borderRadius: 8, padding: 12 }}>
        <legend>允许相互委派的参与者（最多 16 位）</legend>
        {agents.map((agent) => <label key={agent.id} style={{ display: "flex", alignItems: "center", gap: 8, padding: "5px 0" }}>
          <input type="checkbox" style={{ width: "auto" }} checked={allowed.includes(agent.id)}
            disabled={!allowed.includes(agent.id) && allowed.length >= 16}
            onChange={(event) => setSelected(event.target.checked ? [...allowed, agent.id] : allowed.filter((id) => id !== agent.id))} />
          {rosterIdentity(agent)} · {agent.display_name || agent.id}{registeredModel(agent.model).state === "alias" ? "（配置别名，精确型号待确认）" : ""}
        </label>)}
        {!agents.length && <p>名册中尚无可用的 Agent。</p>}
      </fieldset>
      <label htmlFor={`${prefix}-depth`}>最多委派层级</label>
      <input id={`${prefix}-depth`} type="number" min={1} max={3} step={1} required disabled={busy} value={maxDepth} onChange={(event) => setDepth(Number(event.target.value))} />
      <label htmlFor={`${prefix}-children`}>整棵任务树最多子任务数</label>
      <input id={`${prefix}-children`} type="number" min={1} max={12} step={1} required disabled={busy} value={maxChildren} onChange={(event) => setChildren(Number(event.target.value))} />
      <button type="submit" disabled={busy}>{busy ? "正在保存…" : "保存此任务的委派权限"}</button>
      {message && <p role="status">{message}</p>}
    </form>
  </details>;
}
