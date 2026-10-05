import { t, useI18n, getLanguage } from "../i18n";
import React from "react";
import {
  AlertTriangle, Archive, BookOpen, CheckCircle2, Clock3, Database,
  FileStack, HardDrive, Network, RefreshCw, ShieldCheck,
} from "lucide-react";
import { kingdomApi, type KingdomKnowledgeOverview } from "@/lib/kingdom";
import { ConflictsDialog } from "./KingdomConflicts";
import "./kingdom.css";

const fmt = (value?: number) => new Intl.NumberFormat(getLanguage()).format(value ?? 0);
const bytes = (value?: number) => {
  let size = value ?? 0;
  const units = ["B", "KB", "MB", "GB", "TB"];
  let index = 0;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  return `${size.toFixed(index && size < 10 ? 2 : index ? 1 : 0)} ${units[index]}`;
};
const date = (value?: string | null) => value
  ? new Date(value).toLocaleString(getLanguage(), { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false })
  : t("暂无");

function Badge({ children, tone = "neutral" }: { children: React.ReactNode; tone?: string }) {
  useI18n();
  return <span className={`kc-badge kc-badge--${tone}`}>{children}</span>;
}

function Panel({ title, kicker, icon, action, id, children, className = "" }: {
  title: string;
  kicker: string;
  icon: React.ReactNode;
  action?: React.ReactNode;
  id?: string;
  children: React.ReactNode;
  className?: string;
}) {
  useI18n();
  return <section id={id} className={`kc-panel ${className}`}>
    <header className="kc-panel__header">
      <div className="kc-panel__heading"><span className="kc-panel__icon">{icon}</span><div><div className="kc-panel__kicker">{kicker}</div><h2>{title}</h2></div></div>
      {action}
    </header>
    <div className="kc-panel__body">{children}</div>
  </section>;
}

function Metric({ label, value, meta, tone, icon }: {
  label: string;
  value: string;
  meta: string;
  tone: string;
  icon: React.ReactNode;
}) {
  useI18n();
  return <div className={`kc-metric kc-metric--${tone}`}><div className="kc-metric__icon">{icon}</div><div><strong>{value}</strong><h3>{label}</h3><span>{meta}</span></div></div>;
}

export default function KingdomKnowledgePage() {
  useI18n();
  const [data, setData] = React.useState<KingdomKnowledgeOverview | null>(null);
  const [error, setError] = React.useState("");
  const [refreshing, setRefreshing] = React.useState(false);
  const [notice, setNotice] = React.useState<{ text: string; error?: boolean } | null>(null);
  const [section, setSection] = React.useState("overview");
  const [showConflicts, setShowConflicts] = React.useState(false);
  const load = React.useCallback(async () => {
    try {
      setData(await kingdomApi.getKnowledge());
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t("无法加载 SharedKnowledge 状态"));
    }
  }, []);
  React.useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 24 * 60 * 60 * 1000);
    return () => window.clearInterval(timer);
  }, [load]);
  const refresh = React.useCallback(async () => {
    setRefreshing(true);
    try {
      await kingdomApi.refresh();
      await load();
      setNotice({ text: t("四方同步与准入元数据已刷新") });
    } catch (reason) {
      setNotice({ text: reason instanceof Error ? reason.message : t("刷新失败"), error: true });
    } finally {
      setRefreshing(false);
    }
  }, [load]);
  const showSection = React.useCallback((next: string) => {
    setSection(next);
    document.getElementById(`knowledge-${next}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

  if (!data && !error) return <div className="kc-loading"><div><Database size={28} /></div><strong>{t("正在汇总 SharedKnowledge")}</strong><span>{t("读取四方同步与准入元数据")}</span></div>;
  if (!data) return <div className="kc-fatal"><AlertTriangle /><h1>{t("SharedKnowledge 状态暂不可用")}</h1><p>{error}</p><button className="kc-button kc-button--primary" onClick={() => void load()}><RefreshCw size={16} />{t("重试")}</button></div>;

  const vault = data.vault;
  const peers = vault.sync_peers ?? [];
  const admission = vault.admission ?? { available: false };
  const pipeline = vault.insight_pipeline ?? {
    notes: 0, by_status: {}, scored: 0, unscored: 0, complete_structure: 0,
    merge_candidates: 0, overdue: 0, template_available: false, dashboard_available: false,
  };
  const folders = vault.folders ?? [];
  const maxFolder = Math.max(...folders.map((folder) => folder.notes), 1);
  const connected = peers.filter((peer) => peer.connected).length;
  const secretRemaining = admission.output_secret_hits_remaining ?? 0;

  return <div className="kc-page">
    <div className="kc-ambient kc-ambient--one" /><div className="kc-ambient kc-ambient--two" />
    <header className="kc-page-header">
      <div className="kc-page-header__title"><div className="kc-brand-mark"><img src="./retinue-mark-v2.png" alt="" /></div><div><div className="kc-eyebrow">SHARED KNOWLEDGE · FOUR-PEER OB VAULT</div><h1>{t("知识库")}</h1><p>{t("Windows、Node A、Node B、workstation 对等同步；原件先在 Vault 外清洗，合格的结构化 Markdown 才能进入 SharedKnowledge。")}</p></div></div>
      <div className="kc-page-header__tools"><button className="kc-button kc-button--soft" disabled={refreshing} onClick={() => void refresh()}><RefreshCw size={16} className={refreshing ? "kc-spin" : ""} />{refreshing ? t("刷新中") : t("刷新")}</button></div>
    </header>
    <div className="kc-context-bar"><div className="kc-node-filters">{[["overview", t("全局总览")], ["peers", t("4 方对等")], ["admission", t("每日清洗")]].map(([key, label]) => <button key={key} type="button" className={section === key ? "is-active" : ""} aria-pressed={section === key} onClick={() => showSection(key)}>{label}</button>)}</div><div className="kc-freshness"><i className={connected < 4 ? "is-stale" : ""} />Syncthing {connected}/{peers.length || 4} {t("已连接")}</div></div>
    {notice && <div className={`kc-notice ${notice.error ? "kc-notice--error" : "kc-notice--success"}`}><span>{notice.text}</span><button type="button" aria-label={t("关闭提示")} onClick={() => setNotice(null)}>×</button></div>}

    <div className="kc-view">
      <div id="knowledge-overview" className="kc-metrics kc-metrics--compact">
        <Metric label={t("有效笔记")} value={fmt(vault.active_notes)} meta={t("四方共享同一 SharedKnowledge")} icon={<BookOpen />} tone="purple" />
        <Metric label={t("同步节点")} value={`${connected}/${peers.length || 4}`} meta={t("无 canonical / replica")} icon={<Network />} tone={connected === 4 ? "green" : "amber"} />
        <Metric label={t("本轮晋级")} value={fmt(admission.promoted)} meta={t("扫描 {v0} · 拒收 {v1}", { v0: fmt(admission.scanned_documents), v1: fmt(admission.rejected) })} icon={<CheckCircle2 />} tone="blue" />
        <Metric label={t("敏感命中残留")} value={String(secretRemaining)} meta={t("已撤回 {v0}", { v0: fmt(admission.withdrawn) })} icon={<ShieldCheck />} tone={secretRemaining ? "red" : "green"} />
      </div>

      <div id="knowledge-peers" className="kc-layout kc-layout--knowledge">
        <Panel title="SharedKnowledge Obsidian Vault" kicker="PEER-TO-PEER KNOWLEDGE BASE" icon={<Database size={18} />}>
          <div className="kc-vault-hero"><div><strong>{fmt(vault.active_notes)}</strong><span>{t("活跃 Markdown 笔记")}</span></div><div><Badge tone="good">{t("4 方对等")}</Badge><button type="button" className={`kc-badge kc-badge--clickable kc-badge--${(vault.conflicts ?? 0) ? "warn" : "good"}`} onClick={() => setShowConflicts(true)} title={t("点击查看并处理同步冲突")}>{vault.conflicts ?? 0} {t("个冲突")}</button><Badge tone="good">{t("只展示元数据")}</Badge></div></div>
          <div className="kc-vault-meta"><div><HardDrive /><strong>{bytes(vault.active_size_bytes)}</strong><span>{t("活跃库容量")}</span></div><div><FileStack /><strong>{fmt(vault.versions)}</strong><span>{t("保护版本")}</span></div><div><Clock3 /><strong>{date(vault.latest_mtime)}</strong><span>{t("最近更新")}</span></div></div>
          <div className="kc-boundary-note"><ShieldCheck size={16} />{t("Node B 提供版本保护，Node A 承担准入治理，workstation 的 原件仓库保留原件，Windows 是主要 OB 工作端；原件仓库 → Node A 清洗区 → SharedKnowledge Vault，四方均是同步成员。")}</div>
        </Panel>

        <Panel title={t("四方同步节点")} kicker="LIVE SYNCTHING CONNECTIONS" icon={<Network size={18} />}>
          <div className="kc-replica-list">{peers.map((peer) => <article className="kc-replica" key={peer.id}><div><span className={`kc-node-pill kc-node-pill--${peer.id}`}><i />{peer.label}</span><Badge tone={peer.connected && !peer.paused ? "good" : "warn"}>{peer.folder_member === false ? t("未纳入 Vault") : peer.connected ? t("已连接") : t("未连接")}</Badge></div><strong>{peer.role}</strong><span>{peer.tailscale_ip} · {peer.client_version}</span><code>OB · {peer.path_label}</code>{peer.warehouse_path && <code>{t("原件仓库 ·")} {peer.warehouse_path}</code>}</article>)}</div>
        </Panel>
      </div>

      <Panel id="knowledge-admission" title={t("Node A → SharedKnowledge 准入门")} kicker="STAGE · CLEAN · VALIDATE · PROMOTE" icon={<ShieldCheck size={18} />} action={<div className="kc-badge-row"><Badge tone={admission.status === "ok" ? "good" : "warn"}>{admission.status ?? "unknown"}</Badge><Badge tone={secretRemaining ? "bad" : "good"}>{t("输出复扫")} {secretRemaining ? t("阻断") : t("通过")}</Badge></div>}>
        <div className="kc-insight-grid"><div><strong>{fmt(admission.scanned_documents)}</strong><span>{t("候选扫描")}</span></div><div><strong>{fmt(admission.promoted)}</strong><span>{t("清洗晋级")}</span></div><div><strong>{fmt(admission.rejected)}</strong><span>{t("拒收隔离")}</span></div><div><strong>{fmt(admission.withdrawn)}</strong><span>{t("输出撤回")}</span></div><div><strong>{fmt(admission.catalog_projects)}</strong><span>{t("项目目录卡")}</span></div><div><strong>{fmt(admission.pending)}</strong><span>{t("待后续批次")}</span></div></div>
        <div className="kc-boundary-note"><ShieldCheck size={16} />{t("原始数据库、会话、密钥、日志、缓存、代码树、模型、PDF/Office 和压缩包不进入同步 Vault；邮件与手机号先脱敏，内容按哈希去重。策略")} {admission.policy_version ?? t("未报告")}{t("，每日运行一次。")}</div>
      </Panel>

      <Panel title={t("灵感治理流水线")} kicker="NATIVE OB PROPERTIES · TEMPLATES · BASES" icon={<BookOpen size={18} />} action={<div className="kc-badge-row"><Badge tone={pipeline.template_available ? "good" : "warn"}>{t("模板")}{pipeline.template_available ? t("就绪") : t("缺失")}</Badge><Badge tone={pipeline.dashboard_available ? "good" : "warn"}>{t("处理台")}{pipeline.dashboard_available ? t("就绪") : t("缺失")}</Badge></div>}>
        <div className="kc-insight-grid"><div><strong>{pipeline.notes}</strong><span>{t("灵感卡")}</span></div><div><strong>{pipeline.by_status.inbox ?? 0}</strong><span>{t("待整理")}</span></div><div><strong>{pipeline.scored}</strong><span>{t("已评分")}</span></div><div><strong>{pipeline.complete_structure}</strong><span>{t("结构完整")}</span></div><div><strong>{pipeline.merge_candidates}</strong><span>{t("合并候选")}</span></div><div><strong>{pipeline.overdue}</strong><span>{t("逾期复核")}</span></div></div>
      </Panel>

      <div className="kc-layout kc-layout--knowledge-bottom">
        <Panel title={t("知识目录分布")} kicker="TOP FOLDERS" icon={<Archive size={18} />}><div className="kc-folder-bars">{folders.slice(0, 12).map((folder) => <div className="kc-folder-row" key={folder.name}><span>{folder.name}</span><div><i style={{ width: `${Math.max(3, folder.notes / maxFolder * 100)}%` }} /></div><strong>{folder.notes}</strong></div>)}</div></Panel>
        <Panel title={t("数据边界")} kicker="WAREHOUSE ≠ VAULT" icon={<ShieldCheck size={18} />}><div className="kc-principles"><div><strong>{t("workstation / Node A 仓库区")}</strong><span>{t("保存原件、源码、数据集与处理资产")}</span></div><div><strong>{t("Vault 外暂存区")}</strong><span>{t("凭据检测、隐私脱敏、去重、结构化和隔离")}</span></div><div><strong>SharedKnowledge</strong><span>{t("只接收合格 Markdown 与治理元数据，随后四方同步")}</span></div></div></Panel>
      </div>
    </div>
    <ConflictsDialog open={showConflicts} onClose={() => setShowConflicts(false)} onResolved={() => void load()} />
  </div>;
}
