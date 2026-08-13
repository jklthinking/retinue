import type { ReactNode } from "react";

export function PageHeader({
  kicker,
  title,
  subtitle,
  tools,
}: {
  kicker: string;
  title: ReactNode;
  subtitle?: string;
  tools?: ReactNode;
}) {
  return (
    <header className="rt-page-header">
      <div>
        <span className="rt-eyebrow">{kicker}</span>
        <h1>{title}</h1>
        {subtitle && <p>{subtitle}</p>}
      </div>
      {tools && <div className="rt-page-tools">{tools}</div>}
    </header>
  );
}

export function Metric({
  icon,
  label,
  value,
  sub,
  tone = "blue",
  onClick,
}: {
  icon: ReactNode;
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: "blue" | "green" | "amber" | "teal" | "red" | "ink";
  onClick?: () => void;
}) {
  const Tag = onClick ? "button" : "div";
  return (
    <Tag className={`rt-metric rt-metric--${tone}`} onClick={onClick}>
      <span className="rt-metric__icon">{icon}</span>
      <div>
        <strong>{value}</strong>
        <h3>{label}</h3>
        {sub && <span>{sub}</span>}
      </div>
    </Tag>
  );
}

export function Panel({
  icon,
  kicker,
  title,
  tools,
  children,
  className = "",
}: {
  icon: ReactNode;
  kicker: string;
  title: string;
  tools?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`rt-panel ${className}`}>
      <header className="rt-panel__header">
        <div className="rt-panel__heading">
          <span className="rt-panel__icon">{icon}</span>
          <div>
            <span className="rt-eyebrow">{kicker}</span>
            <h2>{title}</h2>
          </div>
        </div>
        {tools}
      </header>
      <div className="rt-panel__body">{children}</div>
    </section>
  );
}

export function Ambient() {
  return (
    <>
      <div className="rt-ambient rt-ambient--one" />
      <div className="rt-ambient rt-ambient--two" />
    </>
  );
}

export function DataState({
  loading,
  error,
  empty,
  stale = false,
  onRetry,
}: {
  loading?: boolean;
  error?: string | null;
  empty?: string;
  stale?: boolean;
  onRetry?: () => void;
}) {
  if (loading) return <p className="rt-data-state">正在读取数据…</p>;
  if (error) {
    return (
      <div className="rt-data-state rt-data-state--error" role="alert">
        <strong>{stale ? "读取失败，数据可能已过期" : "读取失败"}</strong>
        <span>{error}</span>
        {onRetry && <button className="rt-button rt-button--soft" onClick={onRetry}>重试</button>}
      </div>
    );
  }
  return <p className="rt-data-state">{empty ?? "暂无数据"}</p>;
}
