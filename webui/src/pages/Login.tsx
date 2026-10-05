import { FormEvent, useEffect, useState } from "react";
import { Eye, EyeOff, LogIn } from "lucide-react";
import { api, ApiError } from "../api";
import { useVocab } from "../theme";
import { LanguageSwitcher, useI18n } from "../i18n";

interface SiteEntry {
  label: string;
  url: string;
  current: boolean;
}

interface LoginConfig {
  label: string;
  demo: boolean;
  mode: string;
  entry_label: string;
  observer_label?: string;
  footnote: string;
  sites: SiteEntry[];
}

export default function Login({ onLogin }: { onLogin: () => void }) {
  const vocab = useVocab();
  const { t } = useI18n();
  const [config, setConfig] = useState<LoginConfig | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [showManual, setShowManual] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .get<LoginConfig>("/api/login-config")
      .then(setConfig)
      .catch(() => setConfig({ label: "", demo: false, mode: "", entry_label: "", observer_label: "", footnote: "", sites: [] }));
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api.post("/api/auth/login", { username, password });
      onLogin();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("登录失败,请重试"));
    } finally {
      setBusy(false);
    }
  }

  async function demoLogin(seat: "work" | "observe" = "work") {
    setBusy(true);
    setError("");
    try {
      await api.post(`/api/auth/demo-login?seat=${seat}`);
      onLogin();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("进入失败"));
    } finally {
      setBusy(false);
    }
  }

  const demoMode = Boolean(config?.demo) && !showManual;
  const teacherMode = config?.mode === "teacher";
  const entryLabel = config?.entry_label || t(teacherMode ? "一键进入试点" : "一键进入演示");

  return (
    <div className="login-page">
      <div className="login-card">
        <div className="brand login-brand">
          <img className="brand-mark" src="./retinue-mark-v2.png" alt="Retinue" />
          <div>
            <strong>Retinue {vocab.appTitle}</strong>
            <small>{t(teacherMode ? "老师的 AI 备课协作台" : "多智能体协作管理平台")}</small>
          </div>
        </div>
        <LanguageSwitcher />

        {config && config.sites.length > 0 && (
          <div className="site-switch">
            {config.sites.map((site) => (
              <button
                key={site.label}
                type="button"
                className={site.current ? "is-active" : ""}
                onClick={() => {
                  if (!site.current) window.location.href = site.url;
                }}
              >
                {site.label}
              </button>
            ))}
          </div>
        )}

        {demoMode ? (
          <div className="demo-entry">
            <button className="primary demo-button" disabled={busy} onClick={() => void demoLogin("work")}>
              <LogIn size={16} />
              {busy ? t("进入中…") : entryLabel}
            </button>
            {config?.observer_label ? (
              <button type="button" className="link-button" disabled={busy} onClick={() => void demoLogin("observe")}>
                {config.observer_label}
              </button>
            ) : null}
            {error && <p className="error">{error}</p>}
            <button type="button" className="link-button" onClick={() => setShowManual(true)}>
              {t("使用账号密码登录")}
            </button>
          </div>
        ) : (
          <form className="login-form" onSubmit={submit}>
            <label>
              {t("用户名")}
              <input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                required
              />
            </label>
            <label>
              {t("密码")}
              <span className="pw-field">
                <input
                  type={showPassword ? "text" : "password"}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete="current-password"
                  required
                />
                <button
                  type="button"
                  className="pw-toggle"
                  aria-label={t(showPassword ? "隐藏密码" : "显示密码")}
                  onClick={() => setShowPassword((v) => !v)}
                >
                  {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </span>
            </label>
            {error && <p className="error">{error}</p>}
            <button type="submit" className="primary" disabled={busy}>
              {t(busy ? "登录中…" : "登录")}
            </button>
            {Boolean(config?.demo) && (
              <button type="button" className="link-button" onClick={() => setShowManual(false)}>
                {t("返回{label}", { label: entryLabel })}
              </button>
            )}
          </form>
        )}

        <p className="login-footnote">
          {config?.footnote ||
            t(teacherMode
              ? "试点数据独立保存；AI 生成仅使用当前任务内容。"
              : "数据保存在您自己的服务器上,无遥测、无外呼。")}
        </p>
      </div>
    </div>
  );
}
