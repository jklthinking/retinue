import { t, useI18n, getLanguage } from "../i18n";
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
import { isSessionSync, registeredModel, rosterIdentity } from "../lib/rosterIdentity";
import { operationsSystemText } from "../lib/operations";
import "./roster-identity.css";

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
  if (!value) return t("尚无同步记录");
  return new Date(value).toLocaleString(getLanguage(), {
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
  useI18n();
  const vocab = useVocab();
  const [actors, setActors] = useState<ActorInfo[]>([]);
  const [discovery, setDiscovery] = useState<AgentDiscoveryInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [form, setForm] = useState<AgentForm>(EMPTY_FORM);
  const [editingActor, setEditingActor] = useState<string | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const canManage = me.role === "admin" && !me.readonly;
  const canDispatch = me.role !== "viewer" && !me.readonly;

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
      setError(err instanceof ApiError ? err.message : t("智能体发现加载失败"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const agents = actors.filter((actor) => actor.kind === "agent");
  const humans = actors.filter((actor) => actor.kind === "human");
  const syncAgents = agents.filter(isSessionSync);
  const workers = agents.filter((actor) => !isSessionSync(actor));
  const activeAgents = workers.filter((actor) => !actor.disabled);
  const modelsToConfirm = activeAgents.filter((actor) => registeredModel(actor.model).state !== "known");
  const workerAttention = (discovery?.attention || []).filter((item) => {
    const actor = actors.find((candidate) => candidate.id === item.actor_id);
    return actor ? !isSessionSync(actor) : !item.actor_id.endsWith("-session-sync");
  });
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
      setError(err instanceof ApiError ? err.message : t("保存智能体绑定失败"));
    } finally {
      setSaving(false);
    }
  }

  function AgentCard({ actor }: { actor: ActorInfo }) {
  useI18n();
    const sync = isSessionSync(actor);
    const model = registeredModel(actor.model);
    const executor = actor.kind === "agent" && !sync;
    return (
      <article className={"rt-agent " + (actor.disabled ? "is-disabled" : "")}>
        <div className="rt-agent__top">
          <Avatar name={actor.display_name || actor.id} size={40} square />
          <div className="rt-agent__identity">
            <div>
              <h3 title={rosterIdentity(actor)}>{rosterIdentity(actor)}</h3>
              <span className={"rt-dot " + (actor.online ? "is-online" : "")} />
            </div>
            <p>{executor ? t("现有身份 ") + (actor.display_name || actor.id) : sync ? t("会话同步代理") : t("人类成员")}</p>
          </div>
          <span className={"rt-badge " + (actor.online ? "rt-badge--good" : "")}>
            {actor.online ? t("近期认证上报") : t("近期未上报")}
          </span>
        </div>
        {actor.kind === "agent" && <div className="rt-agent__model">
          <Cpu size={12} aria-hidden="true" />
          <span>{t("运行端")} {actor.runtime || t("待绑定")}</span>
          {executor && <span className="roster-model-state" data-state={model.state}>
            {model.state === "alias" ? t("配置别名 · 精确型号待确认") : model.state === "unknown" ? t("型号待确认") : t("登记型号")}
          </span>}
          {sync && actor.model && <span className="roster-model-state">{t("索引版本")} {actor.model}</span>}
        </div>}
        {executor && <p className="roster-registration-note">{t("设备与模型为登记资料；执行时的身份以当次上报为准。")}</p>}
        {(actor.role || actor.goal) && (
          <div className="rt-agent__purpose">
            {actor.role && <strong>{actor.role}</strong>}
            {actor.goal && <span>{actor.goal}</span>}
          </div>
        )}
        <footer>
          <code>{actor.id}</code>
          <span className="muted">{actor.last_seen_at ? timeLabel(actor.last_seen_at) : t("尚未活动")}</span>
          {canManage && actor.kind === "agent" && <button type="button" className="roster-edit-binding"
            aria-label={t("编辑 ") + (actor.display_name || actor.id) + t(" 的绑定")} onClick={() => beginBinding(actor)}>
            <Link2 size={12} aria-hidden="true" /> {t("编辑绑定")} </button>}
        </footer>
      </article>
    );
  }
  return (
    <div className="rt-page roster-page">
      <Ambient />
      <PageHeader
        kicker="AGENT DISCOVERY · RUNTIME BINDING"
        title={t("智能体发现")}
        subtitle={t("以设备与登记模型辨认现有执行成员，运行端辅助定位；型号待确认时先补齐资料，再参与派单。")}
        tools={
          <div className="discovery-header-actions">
            <button
              type="button"
              className="rt-button"
              onClick={() => void load()}
              disabled={loading}
            >
              <RefreshCw className={loading ? "is-spinning" : ""} size={14} />
              {loading ? t("扫描中") : t("扫描并刷新")}
            </button>
            {canDispatch && <button
              type="button"
              className="rt-button rt-button--primary"
              onClick={() => onNavigate("workroom")}
            >
              <Search size={14} /> {t("智能派单")} </button>}
          </div>
        }
      />

      {error && <p className="error">{error}</p>}

      <section className="discovery-overview" aria-label={t("发现概览")}>
        <article>
          <span>{t("执行成员")}</span>
          <strong>{loading && !discovery ? "—" : activeAgents.length}</strong>
          <small>{activeAgents.filter((actor) => actor.online).length} {t("位近期认证上报 · 不含同步代理")}</small>
        </article>
        <article>
          <span>{t("已发现运行时")}</span>
          <strong>{discovery?.runtimes.length ?? "—"}</strong>
          <small>{t("本机扫描、节点探针 + 已同步终端")}</small>
        </article>
        <article className={workerAttention.length > 0 ? "is-attention" : ""}>
          <span>{t("待补齐绑定")}</span>
          <strong>{discovery ? workerAttention.length : "—"}</strong>
          <small>{t("设备、型号、运行端或会话同步")}</small>
        </article>
        <article className={modelsToConfirm.length ? "is-attention" : ""}>
          <span>{t("型号待确认")}</span>
          <strong>{loading && !discovery ? "—" : modelsToConfirm.length}</strong>
          <small>{t("缺少型号或仅登记配置别名")}</small>
        </article>
      </section>

      <Panel
        icon={<Link2 size={15} />}
        kicker="RUNTIME DISCOVERY"
        title={t("已发现的运行时")}
        tools={<span className="discovery-scope">{discovery?.scope ? t(discovery.scope) : t("正在读取…")}</span>}
      >
        <div className="discovery-privacy">
          <ShieldCheck size={14} />
          <span>{discovery?.privacy ? t(discovery.privacy) : t("只读取运行时元数据。")}</span>
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
                    {runtime.registered ? t("已关联成员") : t("待关联")}
                  </span>
                </header>
                <p>
                  {t(runtime.source)}
                  {runtime.path_hint ? " · " + runtime.path_hint : ""}
                  {" · " + runtime.session_count + t(" 条会话索引")}
                </p>
                <small>
                  {runtime.agent_ids.length
                    ? t("成员：") + runtime.agent_ids.map(agentName).join("、")
                    : t("尚未登记接办智能体")}
                  {runtime.nodes.length
                    ? t(" · 节点：") + runtime.nodes.map((node) => node.label).join("、")
                    : ""}
                  {t(" · 探测 ") + timeLabel(runtime.last_probe_at)}
                  {t(" · 最近活动 ") + timeLabel(runtime.last_activity_at)}
                </small>
              </div>
              {!runtime.registered && canManage && (
                <button
                  type="button"
                  className="discovery-link"
                  onClick={() => beginEnroll(runtime)}
                >
                  <Plus size={13} /> {t("登记")} </button>
              )}
            </article>
          ))}
          {!loading && (discovery?.runtimes.length || 0) === 0 && (
            <p className="muted discovery-empty"> {t("暂未发现运行时。安装本地同步器或让已登记智能体同步一条会话元数据后，它会出现在这里。")} </p>
          )}
        </div>
      </Panel>

      <div className="discovery-grid">
        <Panel
          icon={<AlertTriangle size={15} />}
          kicker="BINDING CHECK"
          title={t("需要处理的绑定")}
          className="discovery-attention-panel"
        >
          <div className="discovery-attention-list">
            {workerAttention.map((item) => {
              const actor = actors.find((candidate) => candidate.id === item.actor_id);
              return (
                <article key={item.actor_id}>
                  <Avatar name={item.display_name} size={32} square />
                  <div>
                    <strong>{item.display_name}</strong>
                    <span>{item.missing.map((label) => t(label)).join(getLanguage() === "en" ? ", " : "、")}{t("尚未就绪")}</span>
                  </div>
                  {canManage && actor && (
                    <button type="button" onClick={() => beginBinding(actor)}> {t("补齐")} </button>
                  )}
                </article>
              );
            })}
            {!loading && workerAttention.length === 0 && (
              <p className="muted discovery-empty">{t("所有启用中的智能体均已完成基础绑定。")}</p>
            )}
          </div>
        </Panel>

        <Panel
          icon={<ArrowRight size={15} />}
          kicker="NEXT ACTION"
          title={t("接下来可以做什么")}
          className="discovery-next-panel"
        >
          <ol className="discovery-actions-list">
            {(discovery?.actions || []).map((action) => (
              <li key={action}>{operationsSystemText(action)}</li>
            ))}
            {!discovery && <li>{t("正在汇总发现结果。")}</li>}
          </ol>
          {detectedButUnbound.length > 0 && !canManage && (
            <p className="discovery-role-note">{t("请管理员确认后登记新发现的运行时。")}</p>
          )}
          {canDispatch && <button type="button" className="discovery-dispatch" onClick={() => onNavigate("workroom")}> {t("按能力搜索并派单")} <ArrowRight size={14} />
          </button>}
        </Panel>
      </div>

      {formOpen && canManage && (
        <section className="discovery-form-shell" aria-label={t("智能体运行时绑定")}>
          <form className="discovery-form" onSubmit={saveAgent}>
            <header>
              <div>
                <span>{editingActor ? "BINDING UPDATE" : "NEW AGENT"}</span>
                <h2>{editingActor ? t("编辑设备与模型绑定") : t("登记发现的智能体")}</h2>
              </div>
              <button type="button" className="discovery-close" onClick={closeForm} aria-label={t("关闭")}>
                <X size={16} />
              </button>
            </header>
            <p>
              {editingActor
                ? t("更新会立刻进入派单匹配；不会读取或移动该运行时中的对话。")
                : t("登记后需由该智能体使用自己的令牌同步会话元数据，才能显示认证上报与会话索引状态。")}
            </p>
            <div className="discovery-form-grid">
              <label> {t("标识")} <input
                  value={form.id}
                  onChange={(event) => setField("id", event.target.value)}
                  disabled={Boolean(editingActor)}
                  pattern="[a-z0-9]+(?:-[a-z0-9]+)*"
                  required
                />
              </label>
              <label> {t("显示名称")} <input
                  value={form.display_name}
                  onChange={(event) => setField("display_name", event.target.value)}
                  required
                />
              </label>
              <label> {t("运行端")} <input
                  value={form.runtime}
                  onChange={(event) => setField("runtime", event.target.value)}
                  placeholder="codex、claude-code、kimi…"
                  required
                />
              </label>
              <label> {t("所在设备")} <input
                  value={form.node}
                  onChange={(event) => setField("node", event.target.value)}
                  placeholder="windows、node-d-linux、node-a…"
                  required
                />
              </label>
              <label className="is-wide"> {t("职责（可选）")} <input
                  value={form.role}
                  onChange={(event) => setField("role", event.target.value)}
                  placeholder={t("例如 课程设计")}
                  maxLength={128}
                />
              </label>
              <label className="is-wide"> {t("目标（可选）")} <textarea
                  value={form.goal}
                  onChange={(event) => setField("goal", event.target.value)}
                  placeholder={t("例如 把课程要求转化为可直接使用的教学方案。")}
                  maxLength={500}
                  rows={2}
                />
              </label>
              <label className="is-wide"> {t("登记模型（不确定可留空）")} <input
                  value={form.model}
                  onChange={(event) => setField("model", event.target.value)}
                  placeholder={t("填写已知型号；opus、sonnet 等属于配置别名")}
                />
              </label>
            </div>
            <footer>
              <button type="button" className="rt-button" onClick={closeForm}> {t("取消")} </button>
              <button className="rt-button rt-button--primary" disabled={saving}>
                {saving ? t("保存中…") : editingActor ? t("保存绑定") : t("登记智能体")}
              </button>
            </footer>
          </form>
        </section>
      )}

      <Panel icon={<Bot size={15} />} kicker="AGENTS" title={vocab.membersRoster}>
        <div className="rt-agent-grid">
          {workers.map((actor) => (
            <AgentCard key={actor.id} actor={actor} />
          ))}
          {workers.length === 0 && <p className="muted">{t("尚无执行成员")}</p>}
        </div>
      </Panel>

      {syncAgents.length > 0 && <Panel icon={<RefreshCw size={15} />} kicker="SESSION SYNC" title={t("会话同步代理")}>
        <p className="roster-sync-note">{t("这些身份维护会话索引，不作为独立执行成员，也不计入型号完整度。")}</p>
        <div className="rt-agent-grid">{syncAgents.map((actor) => <AgentCard key={actor.id} actor={actor} />)}</div>
      </Panel>}

      {humans.length > 0 && (
        <Panel icon={<Users size={15} />} kicker="HUMANS" title={t("人类成员")}>
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
