import { t, useI18n, getLanguage } from "../i18n";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { Bot, BookOpen, Check, Copy, Eye, EyeOff, KeyRound, ShieldCheck, UserPlus, X } from "lucide-react";
import { api, ApiError } from "../api";
import type { ActorInfo } from "../types";
import { useVocab } from "../theme";

interface UserRow {
  username: string;
  role: string;
  display_name: string;
  actor_id: string | null;
  disabled: boolean;
}

interface TokenRow {
  id: number;
  actor_id: string;
  label: string;
  disabled: boolean;
  created_at: string | null;
  last_used_at: string | null;
}

interface LicenseInfo {
  present: boolean;
  valid: boolean;
  trial: boolean;
  customer: string | null;
  edition: string | null;
  seats: number | null;
  expires_at: string | null;
  error: string | null;
}

interface StatusInfo {
  version: string;
  license: LicenseInfo;
}

interface OrientationContext {
  schema_version: string;
  generated_at: string;
  status: {
    task_counts: Record<string, number>;
    actors: number;
    online_actors: number;
    skills: number;
    nodes: number;
    knowledge_sources: number;
  };
  rules: string[];
  privacy_boundary: { included: string[]; excluded: string[] };
  markdown: string;
}

interface OnboardingResult {
  status: string;
  actor: ActorInfo;
  account: { username: string; role: string; actor_id: string };
  token: string;
  token_note: string;
  orientation: OrientationContext;
  next_steps: string[];
}

export default function Admin() {
  useI18n();
  const vocab = useVocab();
  const [actors, setActors] = useState<ActorInfo[]>([]);
  const [users, setUsers] = useState<UserRow[]>([]);
  const [tokens, setTokens] = useState<TokenRow[]>([]);
  const [issued, setIssued] = useState("");
  const [message, setMessage] = useState("");
  const [status, setStatus] = useState<StatusInfo | null>(null);
  const [showNewPassword, setShowNewPassword] = useState(false);
  const [showOnboarding, setShowOnboarding] = useState(false);
  const [onboardingContext, setOnboardingContext] = useState<OrientationContext | null>(null);
  const [onboardingResult, setOnboardingResult] = useState<OnboardingResult | null>(null);
  const [onboardingBusy, setOnboardingBusy] = useState(false);
  const [onboardingError, setOnboardingError] = useState("");
  const [copied, setCopied] = useState(false);

  const load = useCallback(async () => {
    const [actorList, userList, tokenList] = await Promise.all([
      api.get<ActorInfo[]>("/api/actors"),
      api.get<UserRow[]>("/api/admin/users"),
      api.get<TokenRow[]>("/api/admin/tokens"),
    ]);
    setActors(actorList);
    setUsers(userList);
    setTokens(tokenList);
    setStatus(await api.get<StatusInfo>("/api/status"));
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function guard(action: () => Promise<void>) {
    setMessage("");
    try {
      await action();
      await load();
    } catch (err) {
      setMessage(err instanceof ApiError ? err.message : t("操作失败"));
    }
  }

  function onCreateActor(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    void guard(async () => {
      await api.post("/api/actors", {
        id: form.get("id"), kind: form.get("kind"), display_name: form.get("display_name") || "",
        role: form.get("actor_role") || "", goal: form.get("goal") || "",
        runtime: form.get("runtime") || "", model: form.get("model") || "", node: form.get("node") || "",
      });
    });
    event.currentTarget.reset();
  }

  function onCreateUser(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    void guard(async () => {
      await api.post("/api/admin/users", {
        username: form.get("username"), password: form.get("password"), role: form.get("role"),
        display_name: form.get("display_name") || "", actor_id: form.get("actor_id") || null,
      });
    });
    event.currentTarget.reset();
  }

  function onCreateToken(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    void guard(async () => {
      const result = await api.post<{ token: string }>("/api/admin/tokens", {
        actor_id: form.get("actor_id"), label: form.get("label") || "",
      });
      setIssued(result.token);
    });
  }

  async function openOnboarding() {
    setShowOnboarding(true);
    setOnboardingResult(null);
    setOnboardingError("");
    try {
      setOnboardingContext(await api.get<OrientationContext>("/api/orientation/context"));
    } catch (err) {
      setOnboardingError(err instanceof ApiError ? err.message : vocab.adminReadError);
    }
  }

  function closeOnboarding() {
    if (!onboardingBusy) setShowOnboarding(false);
  }

  function onPrepareOnboarding(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setOnboardingBusy(true);
    setOnboardingError("");
    void api.post<OnboardingResult>("/api/admin/onboarding/prepare", {
      actor_id: form.get("actor_id"), display_name: form.get("display_name"), runtime: form.get("runtime") || "",
      role: form.get("actor_role") || "", goal: form.get("goal") || "",
      model: form.get("model") || "", node: form.get("node") || "", username: form.get("username"),
      password: form.get("password"), label: form.get("label") || "",
    }).then((result) => {
      setOnboardingResult(result);
      setOnboardingContext(result.orientation);
      void load();
    }).catch((err) => {
      setOnboardingError(err instanceof ApiError ? err.message : t("入职准备失败"));
    }).finally(() => setOnboardingBusy(false));
  }

  async function copyContext() {
    const context = onboardingResult?.orientation.markdown || onboardingContext?.markdown;
    if (!context) return;
    await navigator.clipboard.writeText(context);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  }

  return (
    <div className="rt-page admin-page">
      <div className="page-head admin-page-head">
        <div>
          <span className="rt-eyebrow">ADMINISTRATION</span>
          <h1>{t("管理")}</h1>
          <p className="muted">{vocab.adminBoundary}</p>
        </div>
        <button className="rt-button rt-button--primary" onClick={() => void openOnboarding()}>
          <UserPlus size={16} /> {t("成员入职")} </button>
      </div>
      {message && <p className="error">{message}</p>}

      <section className="admin-section onboarding-section">
        <div className="onboarding-card-icon"><Bot size={19} /></div>
        <div className="onboarding-card-copy">
          <span className="rt-eyebrow">KINGDOM BOOTSTRAP</span>
          <h2>{vocab.onboardTitle}</h2>
          <p>{vocab.onboardDesc}</p>
        </div>
        <button className="rt-button rt-button--soft" onClick={() => void openOnboarding()}> {t("打开入职向导")} <span aria-hidden="true">→</span>
        </button>
      </section>

      {status && (
        <section className="admin-section">
          <h2>{t("版本与支持证书")}</h2>
          <p><span className="chip chip-low">{t("MIT 开源版")}</span> {t("允许个人与商业使用，无需激活（服务端 v")}{status.version}）。</p>
          {status.license.valid ? (
            <p><span className="chip chip-low">{t("支持证书有效")}</span> {status.license.customer} · {status.license.edition} · {status.license.seats} {t("席 · 有效期至")} {status.license.expires_at}{t("；证书仅提供支持信息。")}</p>
          ) : (
            <p className="muted">{status.license.error ? t("可选支持证书异常：{v0}", { v0: status.license.error }) : t("未安装可选支持证书。")} {t("不影响 MIT 版的功能或使用权。")}</p>
          )}
        </section>
      )}

      <section className="admin-section">
        <h2>{t("执行者（Actors）")}</h2>
        <form className="admin-form" onSubmit={onCreateActor}>
          <input name="id" placeholder={t("slug，例如 scribe-c")} pattern="[a-z0-9]+(-[a-z0-9]+)*" required />
          <select name="kind" defaultValue="agent"><option value="agent">{t("智能体")}</option><option value="human">{t("人类")}</option></select>
          <input name="display_name" placeholder={t("显示名")} />
          <input name="actor_role" placeholder={t("职责，例如课程设计")} maxLength={128} />
          <input name="goal" placeholder={t("目标，例如交付可直接使用的教学方案")} maxLength={500} />
          <input name="runtime" placeholder={t("运行时，例如 claude-code")} />
          <input name="model" placeholder={t("模型")} />
          <input name="node" placeholder={t("节点")} />
          <button className="primary">{t("添加")}</button>
        </form>
        <table className="admin-table"><thead><tr><th>{t("标识")}</th><th>{t("类型")}</th><th>{t("显示名")}</th><th>{t("职责")}</th><th>{t("运行时")}</th><th>{t("节点")}</th></tr></thead><tbody>
          {actors.map((a) => <tr key={a.id}><td>{a.id}</td><td>{a.kind === "agent" ? t("智能体") : t("人类")}</td><td>{a.display_name}</td><td>{a.role || "—"}</td><td>{a.runtime || "—"}</td><td>{a.node || "—"}</td></tr>)}
        </tbody></table>
      </section>

      <section className="admin-section">
        <h2>{t("登录账号")}</h2>
        <form className="admin-form" onSubmit={onCreateUser}>
          <input name="username" placeholder={t("用户名")} required minLength={2} />
          <span className="pw-field"><input name="password" type={showNewPassword ? "text" : "password"} placeholder={t("密码（≥8位）")} required minLength={8} /><button type="button" className="pw-toggle" aria-label={showNewPassword ? t("隐藏密码") : t("显示密码")} onClick={() => setShowNewPassword((v) => !v)}>{showNewPassword ? <EyeOff size={16} /> : <Eye size={16} />}</button></span>
          <select name="role" defaultValue="member"><option value="member">{t("成员")}</option><option value="viewer">{t("观察席（只读）")}</option><option value="admin">{t("管理员")}</option></select>
          <input name="display_name" placeholder={t("显示名")} />
          <select name="actor_id" defaultValue=""><option value="">{t("不绑定执行者")}</option>{actors.map((a) => <option key={a.id} value={a.id}>{a.display_name || a.id}</option>)}</select>
          <button className="primary">{t("创建")}</button>
        </form>
        <table className="admin-table"><thead><tr><th>{t("用户名")}</th><th>{t("角色")}</th><th>{t("显示名")}</th><th>{t("绑定执行者")}</th></tr></thead><tbody>
          {users.map((u) => <tr key={u.username}><td>{u.username}</td><td>{u.role === "admin" ? t("管理员") : u.role === "viewer" ? t("观察席") : t("成员")}</td><td>{u.display_name}</td><td>{u.actor_id || "—"}</td></tr>)}
        </tbody></table>
      </section>

      <section className="admin-section">
        <h2>{t("智能体接入令牌")}</h2>
        <p className="muted">{t("令牌用于智能体经 API/MCP 领卡与回执；向导会在入职时自动发放，并且只展示一次。")}</p>
        <form className="admin-form" onSubmit={onCreateToken}><select name="actor_id" required>{actors.filter((a) => a.kind === "agent").map((a) => <option key={a.id} value={a.id}>{a.display_name || a.id}</option>)}</select><input name="label" placeholder={t("用途备注")} /><button className="primary">{t("签发令牌")}</button></form>
        {issued && <p className="token-issued">{t("新令牌仅此一次展示，请立即保存：")}<code>{issued}</code></p>}
        <table className="admin-table"><thead><tr><th>{t("执行者")}</th><th>{t("备注")}</th><th>{t("签发时间")}</th><th>{t("最近使用")}</th></tr></thead><tbody>{tokens.map((t) => <tr key={t.id}><td>{t.actor_id}</td><td>{t.label || "—"}</td><td>{t.created_at ? new Date(t.created_at).toLocaleString(getLanguage()) : "—"}</td><td>{t.last_used_at ? new Date(t.last_used_at).toLocaleString(getLanguage()) : "—"}</td></tr>)}</tbody></table>
      </section>

      {showOnboarding && <div className="drawer-mask" onMouseDown={(event) => { if (event.target === event.currentTarget) closeOnboarding(); }}>
        <aside className="drawer onboarding-drawer" aria-label={t("新成员入职")}>
          <div className="drawer-head"><div><span className="rt-eyebrow">NEW MEMBER</span><h2>{t("新成员入职")}</h2><p className="drawer-sub">{t("先登记身份，再生成只读上下文包")}</p></div><button className="close" onClick={closeOnboarding} aria-label={t("关闭")}><X size={20} /></button></div>
          {onboardingError && <p className="blocked-banner">{onboardingError}</p>}
          {!onboardingResult ? <>
            <form className="onboarding-form" onSubmit={onPrepareOnboarding}>
              <div className="onboarding-form-heading"><Bot size={16} /><span>{t("身份资料")}</span></div>
              <label>{t("执行者标识")}<input name="actor_id" placeholder={t("例如 worker-scribe")} pattern="[a-z0-9]+(-[a-z0-9]+)*" required /></label>
              <label>{t("显示名")}<input name="display_name" placeholder={t("例如节点 A 写作助手")} required /></label>
              <label>{t("职责")}<input name="actor_role" placeholder={t("例如 写作与编辑")} maxLength={128} /></label>
              <label>{t("目标")}<textarea name="goal" placeholder={t("例如 把业务要求整理成清晰、可交付的内容。")} maxLength={500} rows={2} /></label>
              <div className="onboarding-grid"><label>{t("运行时")}<input name="runtime" placeholder="claude-code" /></label><label>{t("模型")}<input name="model" placeholder="claude-sonnet" /></label></div>
              <label>{t("所在节点")}<input name="node" placeholder={t("可稍后绑定")} /></label>
              <div className="onboarding-form-heading"><KeyRound size={16} /><span>{t("登录与接入")}</span></div>
              <label>{t("登录用户名")}<input name="username" placeholder={t("用于网页管理")} required minLength={2} /></label>
              <label>{t("初始密码")}<input name="password" type="password" placeholder={t("至少 8 位")} required minLength={8} /></label>
              <label>{t("令牌备注")}<input name="label" placeholder={t("例如 首次接入")} /></label>
              <button className="rt-button rt-button--primary onboarding-submit" disabled={onboardingBusy}>{onboardingBusy ? t("正在生成…") : t("准备入职并生成上下文")}</button>
            </form>
            <div className="onboarding-preview"><div className="onboarding-form-heading"><BookOpen size={16} /><span>{t("入职前会看到什么")}</span></div>{onboardingContext ? <><div className="context-stats"><span><strong>{onboardingContext.status.actors}</strong> {t("成员")}</span><span><strong>{onboardingContext.status.nodes}</strong> {t("节点")}</span><span><strong>{onboardingContext.status.skills}</strong> {t("技能")}</span><span><strong>{onboardingContext.status.knowledge_sources}</strong> {t("知识源")}</span></div><ul>{onboardingContext.rules.slice(0, 3).map((rule) => <li key={rule}>{rule}</li>)}</ul><p className="muted">{t("隐私边界：不包含会话正文、私有记忆、提示词、密码或令牌。")}</p></> : <p className="muted">{vocab.adminLoading}</p>}</div>
          </> : <div className="onboarding-result">
            <div className="success-mark"><Check size={18} /></div><h3>{t("入职资料已准备好")}</h3><p className="muted">{onboardingResult.actor.display_name} {t("已登记为")} {onboardingResult.actor.id}{t("。生产 Profile 写入仍需单独确认。")}</p>
            <div className="result-block"><span className="result-label">{t("一次性接入令牌")}</span><code>{onboardingResult.token}</code><small>{onboardingResult.token_note}</small></div>
            <div className="result-block"><span className="result-label">{t("给 BOT 的第一句话")}</span><p>{t("使用自己的 Bearer 令牌调用")} <code>GET /api/orientation/context</code>{t("，每次启动或收到新任务时刷新。")}</p></div>
            <div className="result-block context-export"><div className="result-label-row"><span className="result-label">{vocab.contextPackLabel}</span><button className="rt-button rt-button--soft" onClick={() => void copyContext()}>{copied ? <Check size={14} /> : <Copy size={14} />} {copied ? t("已复制") : t("复制")}</button></div><textarea readOnly value={onboardingResult.orientation.markdown} /></div>
            <div className="next-steps"><ShieldCheck size={16} /><div><strong>{t("下一步")}</strong>{onboardingResult.next_steps.map((step) => <p key={step}>{step}</p>)}</div></div>
            <button className="rt-button rt-button--soft onboarding-close" onClick={closeOnboarding}>{t("完成")}</button>
          </div>}
        </aside>
      </div>}
    </div>
  );
}
