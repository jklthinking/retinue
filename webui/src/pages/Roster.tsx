import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  ArrowRight,
  Bot,
  Cpu,
  Link2,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  Users,
  X,
} from "lucide-react";
import { api, ApiError } from "../api";
import type {
  ActorInfo,
  AgentDiscoveryInfo,
  Me,
  RuntimeDiscoveryInfo,
} from "../types";
import { Ambient, PageHeader, Panel } from "../components/ui";
import { Avatar } from "../avatar";
import { useVocab } from "../theme";

type AgentForm = {
  id: string;
  display_name: string;
  role: string;
  goal: string;
  runtime: string;
  model: string;
  node: string;
};

const EMPTY_FORM: AgentForm = {
  id: "",
  display_name: "",
  role: "",
  goal: "",
  runtime: "",
  model: "",
  node: "",
};

function timeLabel(value: string | null): string {
  if (!value) return "尚无同步记录";
  return new Date(value).toLocaleString("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

export default function Roster({
  me,
  onNavigate,
}: {
  me: Me;
  onNavigate: (page: "workroom" | "admin") => void;
}) {
  const vocab = useVocab();
  const [actors, setActors] = useState<ActorInfo[]>([]);
  const [discovery, setDiscovery] = useState<AgentDiscoveryInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [form, setForm] = useState<AgentForm>(EMPTY_FORM);
  const [editingActor, setEditingActor] = useState<string | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const canManage = me.role === "admin";

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [actorRows, discoveryRows] = await Promise.all([
        api.get<ActorInfo[]>("/api/actors"),
        api.get<AgentDiscoveryInfo>("/api/agent-discovery"),
      ]);
      setActors(actorRows);
      setDiscovery(discoveryRows);
      setError("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "智能体发现加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const agents = actors.filter((actor) => actor.kind === "agent");
  const humans = actors.filter((actor) => actor.kind === "human");
  const activeAgents = agents.filter((actor) => !actor.disabled);
  const agentName = useCallback(
    (id: string) => actors.find((actor) => actor.id === id)?.display_name || id,
    [actors]
  );

  const detectedButUnbound = useMemo(
    () => (discovery?.runtimes || []).filter((runtime) => !runtime.registered),
    [discovery]
  );

  function setField(field: keyof AgentForm, value: string) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  function beginEnroll(runtime: RuntimeDiscoveryInfo) {
    if (!canManage) return;
    setEditingActor(null);
    setForm({
      id: runtime.runtime + "-agent",
      display_name: runtime.label + " 助理",
      role: "",
      goal: "",
      runtime: runtime.runtime,
      model: "",
      node: "",
    });
    setFormOpen(true);
    setError("");
  }

  function beginBinding(actor: ActorInfo) {
    if (!canManage) return;
    setEditingActor(actor.id);
    setForm({
      id: actor.id,
      display_name: actor.display_name,
      role: actor.role,
      goal: actor.goal,
      runtime: actor.runtime,
      model: actor.model,
      node: actor.node,
    });
    setFormOpen(true);
    setError("");
  }

  function closeForm() {
    setFormOpen(false);
    setEditingActor(null);
    setForm(EMPTY_FORM);
  }

  async function saveAgent(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canManage) return;
    setSaving(true);
    try {
      if (editingActor) {
        await api.post("/api/actors/" + editingActor + "/update", {
          display_name: form.display_name.trim(),
          role: form.role.trim(),
          goal: form.goal.trim(),
          runtime: form.runtime.trim(),
          model: form.model.trim(),
          node: form.node.trim(),
        });
      } else {
        await api.post("/api/actors", {
          id: form.id.trim(),
          kind: "agent",
          display_name: form.display_name.trim(),
          role: form.role.trim(),
          goal: form.goal.trim(),
          runtime: form.runtime.trim(),
          model: form.model.trim(),
          node: form.node.trim(),
        });
      }
      closeForm();
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "保存智能体绑定失败");
    } finally {
      setSaving(false);
    }
  }

  function AgentCard({ actor }: { actor: ActorInfo }) {
    return (
      <article className={"rt-agent " + (actor.disabled ? "is-disabled" : "")}>
        <div className="rt-agent__top">
          <Avatar name={actor.display_name || actor.id} size={40} square />
          <div className="rt-agent__identity">
            <div>
              <h3>{actor.display_name || actor.id}</h3>
              <span className={"rt-dot " + (actor.online ? "is-online" : "")} />
            </div>
            <p>
              {actor.kind === "agent" ? "智能体" : "人类成员"}
              {actor.node ? " · " + actor.node : ""}
            </p>
          </div>
          <span className={"rt-badge " + (actor.online ? "rt-badge--good" : "")}>
            {actor.online ? "在线" : "离线"}
          </span>
        </div>
        {(actor.runtime || actor.model) && (
          <div className="rt-agent__model">
            <Cpu size={12} />
            {actor.runtime || "—"}
            {actor.model && <em>{actor.model}</em>}
          </div>
        )}
        {(actor.role || actor.goal) && (
          <div className="rt-agent__purpose">
            {actor.role && <strong>{actor.role}</strong>}
            {actor.goal && <span>{actor.goal}</span>}
          </div>
        )}
        <footer>
          <code>{actor.id}</code>
          <span className="muted">
            {actor.last_seen_at ? timeLabel(actor.last_seen_at) : "尚未活动"}
          </span>
        </footer>
      </article>
    );
  }

  return (
    <div className="rt-page">
      <Ambient />
      <PageHeader
        kicker="AGENT DISCOVERY · RUNTIME BINDING"
        title="智能体发现"
        subtitle="发现已接入终端的运行时，确认成员绑定后即可按技能、负载与可用性派单。"
        tools={
          <div className="discovery-header-actions">
            <button
              type="button"
              className="rt-button"
              onClick={() => void load()}
              disabled={loading}
            >
              <RefreshCw className={loading ? "is-spinning" : ""} size={14} />
              {loading ? "扫描中" : "扫描并刷新"}
            </button>
            <button
              type="button"
              className="rt-button rt-button--primary"
              onClick={() => onNavigate("workroom")}
            >
              <Search size={14} /> 智能派单
            </button>
          </div>
        }
      />

      {error && <p className="error">{error}</p>}

      <section className="discovery-overview" aria-label="发现概览">
        <article>
          <span>已连接智能体</span>
          <strong>{activeAgents.length}</strong>
          <small>{agents.filter((actor) => actor.online).length} 位在线</small>
        </article>
        <article>
          <span>已发现运行时</span>
          <strong>{discovery?.runtimes.length ?? "—"}</strong>
          <small>本机扫描、节点探针 + 已同步终端</small>
        </article>
        <article className={(discovery?.attention.length || 0) > 0 ? "is-attention" : ""}>
          <span>待补齐绑定</span>
          <strong>{discovery?.attention.length ?? "—"}</strong>
          <small>运行时、节点或会话同步</small>
        </article>
      </section>

      <Panel
        icon={<Link2 size={15} />}
        kicker="RUNTIME DISCOVERY"
        title="已发现的运行时"
        tools={<span className="discovery-scope">{discovery?.scope || "正在读取…"}</span>}
      >
        <div className="discovery-privacy">
          <ShieldCheck size={14} />
          <span>{discovery?.privacy || "只读取运行时元数据。"}</span>
        </div>
        <div className="discovery-runtime-list">
          {(discovery?.runtimes || []).map((runtime) => (
            <article className="discovery-runtime" key={runtime.runtime}>
              <div className="discovery-runtime__mark">
                <Bot size={16} />
              </div>
              <div className="discovery-runtime__main">
                <header>
                  <strong>{runtime.label}</strong>
                  <span className={runtime.registered ? "is-ready" : "is-new"}>
                    {runtime.registered ? "已关联成员" : "待关联"}
                  </span>
                </header>
                <p>
                  {runtime.source}
                  {runtime.path_hint ? " · " + runtime.path_hint : ""}
                  {" · " + runtime.session_count + " 条会话索引"}
                </p>
                <small>
                  {runtime.agent_ids.length
                    ? "成员：" + runtime.agent_ids.map(agentName).join("、")
                    : "尚未登记接办智能体"}
                  {runtime.nodes.length
                    ? " · 节点：" + runtime.nodes.map((node) => node.label).join("、")
                    : ""}
                  {" · 探测 " + timeLabel(runtime.last_probe_at)}
                  {" · 最近活动 " + timeLabel(runtime.last_activity_at)}
                </small>
              </div>
              {!runtime.registered && canManage && (
                <button
                  type="button"
                  className="discovery-link"
                  onClick={() => beginEnroll(runtime)}
                >
                  <Plus size={13} /> 登记
                </button>
              )}
            </article>
          ))}
          {!loading && (discovery?.runtimes.length || 0) === 0 && (
            <p className="muted discovery-empty">
              暂未发现运行时。安装本地同步器或让已登记智能体同步一条会话元数据后，它会出现在这里。
            </p>
          )}
        </div>
      </Panel>

      <div className="discovery-grid">
        <Panel
          icon={<AlertTriangle size={15} />}
          kicker="BINDING CHECK"
          title="需要处理的绑定"
          className="discovery-attention-panel"
        >
          <div className="discovery-attention-list">
            {(discovery?.attention || []).map((item) => {
              const actor = actors.find((candidate) => candidate.id === item.actor_id);
              return (
                <article key={item.actor_id}>
                  <Avatar name={item.display_name} size={32} square />
                  <div>
                    <strong>{item.display_name}</strong>
                    <span>{item.missing.join("、")}尚未就绪</span>
                  </div>
                  {canManage && actor && (
                    <button type="button" onClick={() => beginBinding(actor)}>
                      补齐
                    </button>
                  )}
                </article>
              );
            })}
            {!loading && (discovery?.attention.length || 0) === 0 && (
              <p className="muted discovery-empty">所有启用中的智能体均已完成基础绑定。</p>
            )}
          </div>
        </Panel>

        <Panel
          icon={<ArrowRight size={15} />}
          kicker="NEXT ACTION"
          title="接下来可以做什么"
          className="discovery-next-panel"
        >
          <ol className="discovery-actions-list">
            {(discovery?.actions || []).map((action) => (
              <li key={action}>{action}</li>
            ))}
            {!discovery && <li>正在汇总发现结果。</li>}
          </ol>
          {detectedButUnbound.length > 0 && !canManage && (
            <p className="discovery-role-note">请管理员确认后登记新发现的运行时。</p>
          )}
          <button type="button" className="discovery-dispatch" onClick={() => onNavigate("workroom")}>
            按能力搜索并派单 <ArrowRight size={14} />
          </button>
        </Panel>
      </div>

      {formOpen && (
        <section className="discovery-form-shell" aria-label="智能体运行时绑定">
          <form className="discovery-form" onSubmit={saveAgent}>
            <header>
              <div>
                <span>{editingActor ? "BINDING UPDATE" : "NEW AGENT"}</span>
                <h2>{editingActor ? "补齐智能体绑定" : "登记发现的智能体"}</h2>
              </div>
              <button type="button" className="discovery-close" onClick={closeForm} aria-label="关闭">
                <X size={16} />
              </button>
            </header>
            <p>
              {editingActor
                ? "更新会立刻进入派单匹配；不会读取或移动该运行时中的对话。"
                : "登记后需由该智能体使用自己的令牌同步会话元数据，才能显示真实在线与会话状态。"}
            </p>
            <div className="discovery-form-grid">
              <label>
                标识
                <input
                  value={form.id}
                  onChange={(event) => setField("id", event.target.value)}
                  disabled={Boolean(editingActor)}
                  pattern="[a-z0-9]+(?:-[a-z0-9]+)*"
                  required
                />
              </label>
              <label>
                显示名称
                <input
                  value={form.display_name}
                  onChange={(event) => setField("display_name", event.target.value)}
                  required
                />
              </label>
              <label>
                运行时
                <input
                  value={form.runtime}
                  onChange={(event) => setField("runtime", event.target.value)}
                  placeholder="codex、claude-code、kimi…"
                  required
                />
              </label>
              <label>
                所在节点
                <input
                  value={form.node}
                  onChange={(event) => setField("node", event.target.value)}
                  placeholder="windows、forge-linux、throne…"
                  required
                />
              </label>
              <label className="is-wide">
                职责（可选）
                <input
                  value={form.role}
                  onChange={(event) => setField("role", event.target.value)}
                  placeholder="例如 课程设计"
                  maxLength={128}
                />
              </label>
              <label className="is-wide">
                目标（可选）
                <textarea
                  value={form.goal}
                  onChange={(event) => setField("goal", event.target.value)}
                  placeholder="例如 把课程要求转化为可直接使用的教学方案。"
                  maxLength={500}
                  rows={2}
                />
              </label>
              <label className="is-wide">
                模型（可选）
                <input
                  value={form.model}
                  onChange={(event) => setField("model", event.target.value)}
                  placeholder="例如 gpt-5.6"
                />
              </label>
            </div>
            <footer>
              <button type="button" className="rt-button" onClick={closeForm}>
                取消
              </button>
              <button className="rt-button rt-button--primary" disabled={saving}>
                {saving ? "保存中…" : editingActor ? "保存绑定" : "登记智能体"}
              </button>
            </footer>
          </form>
        </section>
      )}

      <Panel icon={<Bot size={15} />} kicker="AGENTS" title={vocab.membersRoster}>
        <div className="rt-agent-grid">
          {agents.map((actor) => (
            <AgentCard key={actor.id} actor={actor} />
          ))}
          {agents.length === 0 && <p className="muted">尚无智能体</p>}
        </div>
      </Panel>

      {humans.length > 0 && (
        <Panel icon={<Users size={15} />} kicker="HUMANS" title="人类成员">
          <div className="rt-agent-grid">
            {humans.map((actor) => (
              <AgentCard key={actor.id} actor={actor} />
            ))}
          </div>
        </Panel>
      )}
    </div>
  );
}
