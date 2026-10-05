import { t, useI18n, getLanguage } from "../i18n";
import { useCallback, useState } from "react";
import { CheckCircle2, Database, FileText, Gauge, GitBranch, LockKeyhole, RefreshCw, ShieldCheck, Sparkles, Table2, Wrench } from "lucide-react";
import { api } from "../api";
import { Ambient, Metric, PageHeader, Panel } from "../components/ui";
import { useVocab } from "../theme";
import { operationsSystemText, sourceTime, useOperationsRead } from "../lib/operations";
import { requestDataRefresh } from "../lib/refresh";
import "./operations.css";

interface CatalogLayer {
  key: string;
  title: string;
  table: string;
  rows: number;
  status: "good" | "attention" | "info";
  fields: string[];
}
interface QualityCheck {
  key: string;
  label: string;
  observed: number;
  total: number;
  status: "good" | "attention" | "info";
  detail: string;
}
interface HealthCheck extends QualityCheck {
  issue_count?: number;
  retained?: number;
  truncated?: boolean;
  items: Array<{ kind: string; id: string; node?: string; actor_id?: string; runtime?: string; state: string; observed_at?: string | null; age_seconds?: number | null }>;
}
interface DataCatalogInfo {
  schema_version: string;
  generated_at: string;
  storage_contract: {
    documents: string;
    operational: string;
    json_fields: string[];
    canonical: string;
  };
  summary: {
    tasks: number;
    actors: number;
    skills: number;
    nodes: number;
    knowledge_sources: number;
    sessions: number;
    events: number;
    pipeline_templates: number;
    quality_score: number;
  };
  layers: CatalogLayer[];
  quality: { score: number; checks: QualityCheck[] };
  recommendations: string[];
  privacy: { web_catalog: string; excluded: string[] };
  health?: { generated_at: string; mode: "read_only"; history_policy: string; thresholds_seconds: Record<string, number>; checks: HealthCheck[] };
}

const fmt = (value: number) => value.toLocaleString(getLanguage());
const date = (value: string) => new Date(value).toLocaleString(getLanguage(), { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });

export default function DataCatalog() {
  useI18n();
  const vocab = useVocab();
  const reader = useCallback(() => api.get<DataCatalogInfo>("/api/data-catalog"), []);
  const { data, error, refreshing, fetchedAt } = useOperationsRead("data-catalog", reader);
  const [section, setSection] = useState<"layers" | "quality" | "contract">("layers");

  if (!data && !error) return <div className="rt-loading"><Database size={25} />{vocab.dataCatalogLoading}</div>;
  if (!data) return <div className="rt-empty-state"><Database size={25} /><strong>{t("数据目录暂不可用")}</strong><span>{error}</span><button className="rt-button rt-button--primary" onClick={requestDataRefresh}>{t("重试")}</button></div>;

  const summary = data.summary;
  return (
    <div className="rt-page data-catalog-page">
      <Ambient />
      <PageHeader
        kicker="RETINUE · DATA WORKBENCH"
        title={t("数据整理台")}
        subtitle={t("把数据库、Obsidian 和智能体注册表放进同一张可解释的地图 · 检查生成 {v0} · 页面读取 {v1}", { v0: date(data.generated_at), v1: sourceTime(fetchedAt) })}
        tools={<button className="rt-button rt-button--soft" disabled={refreshing} onClick={requestDataRefresh}><RefreshCw size={15} className={refreshing ? "rt-spin" : ""} />{refreshing ? t("刷新中") : t("刷新目录")}</button>}
      />
      {error && <p className="ops-warning" role="alert">{t("读取失败，保留上次目录；来源可能已变化：")}{error}</p>}

      <div className="data-catalog-tabs" role="tablist" aria-label={t("数据整理台视图")}>
        {[['layers', t("数据层"), <Database size={14} />], ['quality', t("质量检查"), <Gauge size={14} />], ['contract', t("格式与边界"), <ShieldCheck size={14} />]].map(([key, label, icon]) => <button key={String(key)} role="tab" aria-selected={section === key} className={section === key ? "is-active" : ""} onClick={() => setSection(key as typeof section)}>{icon}<span>{label}</span></button>)}
        <span className="data-catalog-version">{data.schema_version}</span>
      </div>

      <div className="rt-metrics data-catalog-metrics">
        <Metric icon={<Table2 size={16} />} label={t("任务卡")} value={fmt(summary.tasks)} sub={t("{v0} 条事件链", { v0: fmt(summary.events) })} tone="blue" />
        <Metric icon={<Sparkles size={16} />} label={t("技能注册")} value={fmt(summary.skills)} sub={t("{v0} 个成员", { v0: fmt(summary.actors) })} tone="amber" />
        <Metric icon={<GitBranch size={16} />} label={t("知识与节点")} value={`${summary.knowledge_sources} / ${summary.nodes}`} sub={t("知识源 / 基础设施")} tone="teal" />
        <Metric icon={<CheckCircle2 size={16} />} label={t("基础结构完整度")} value={`${summary.quality_score}%`} sub={t("{v0} 条流程模板", { v0: summary.pipeline_templates })} tone={summary.quality_score >= 80 ? "green" : "amber"} />
      </div>
      <p className="ops-source-note">{t("基础结构完整度仅衡量格式与关联检查，不代表来源新鲜度；身份、重复候选与采集异常在「质量检查 → 运行数据健康」单列展示。")}</p>

      {section === "layers" && <>
        <Panel icon={<Database size={15} />} kicker="STORAGE LAYERS" title={vocab.dataLayerTitle} tools={<span className="data-catalog-panel-note">{t("只展示目录与字段，不读取原始正文")}</span>}>
          <div className="data-layer-list">{data.layers.map((layer) => <article className="data-layer-row" key={layer.key}><div className="data-layer-icon"><Database size={16} /></div><div className="data-layer-main"><div className="data-layer-title"><strong>{t(layer.title)}</strong><span className={`data-status data-status--${layer.status}`}>{layer.status === "good" ? t("已纳入") : layer.status === "attention" ? t("待整理") : t("信息层")}</span></div><span className="data-layer-table">{layer.table}</span><div className="data-field-list">{layer.fields.map((field) => <code key={field}>{field}</code>)}</div></div><div className="data-layer-count"><strong>{fmt(layer.rows)}</strong><span>{t("记录")}</span></div><div className={`data-readonly-toggle ${layer.status !== "attention" ? "is-on" : ""}`} role="img" aria-label={layer.status !== "attention" ? t("已纳入目录") : t("待整理")}><i /></div></article>)}</div>
          <div className="data-catalog-boundary"><LockKeyhole size={15} /><span>{vocab.dataBoundary}</span></div>
        </Panel>
        <div className="data-catalog-grid-two"><Panel icon={<Wrench size={15} />} kicker="NEXT REFINEMENT" title={t("建议整理动作")}><div className="data-recommendations">{data.recommendations.map((item, index) => <div key={item}><span>{index + 1}</span><p>{operationsSystemText(item)}</p></div>)}</div></Panel><Panel icon={<FileText size={15} />} kicker="DOCUMENT PIPELINE" title={t("文件流转")}><div className="data-flow"><span className="data-flow-step data-flow-step--input">{t("输入")}</span><b>→</b><span className="data-flow-step data-flow-step--clean">{t("清洗")}</span><b>→</b><span className="data-flow-step data-flow-step--index">{t("索引")}</span><b>→</b><span className="data-flow-step data-flow-step--output">{t("输出")}</span></div><p className="muted">{t("原始纪要先进 Inbox；确认后的结构化 Markdown 才进入知识库，回答和交付物通过双链回指来源。")}</p></Panel></div>
      </>}

      {section === "quality" && <Panel icon={<Gauge size={15} />} kicker="DATA QUALITY GATE" title={t("基础字段与关联检查")} tools={<span className="data-score">{t("基础结构得分")} {data.quality.score}%</span>}><div className="quality-list">{data.quality.checks.map((check) => { const ratio = check.total ? Math.round(check.observed / check.total * 100) : 100; return <article className="quality-row" key={check.key}><div className={`quality-icon quality-icon--${check.status}`}>{check.status === "good" ? <CheckCircle2 size={15} /> : <Wrench size={15} />}</div><div className="quality-main"><div className="quality-head"><strong>{t(check.label)}</strong><span>{check.total ? `${fmt(check.observed)} / ${fmt(check.total)}` : t("暂无记录")}</span></div><div className="quality-bar"><i style={{ width: `${Math.max(4, ratio)}%` }} /></div><p>{t(check.detail)}</p></div></article>; })}</div><div className="data-catalog-boundary"><ShieldCheck size={15} /><span>{t("结果指标先于“完成”标签：任务完成率、验收条件覆盖率、事件链完整率和数据新鲜度都应可查询。")}</span></div></Panel>}

      {section === "contract" && <div className="data-catalog-grid-two"><Panel icon={<FileText size={15} />} kicker="CANONICAL FORMAT" title={t("格式分工")}><dl className="contract-list"><div><dt>{t("纪要与知识")}</dt><dd>{t(data.storage_contract.documents)}</dd></div><div><dt>{t("任务与运行")}</dt><dd>{t(data.storage_contract.operational)}</dd></div><div><dt>{t("当前真相源")}</dt><dd>{t(data.storage_contract.canonical)}</dd></div></dl></Panel><Panel icon={<LockKeyhole size={15} />} kicker="PRIVACY BOUNDARY" title={t("网页可见边界")}><p className="data-catalog-privacy">{t(data.privacy.web_catalog)}</p><div className="privacy-excluded">{data.privacy.excluded.map((item) => <span key={item}>{t("不展示 ·")} {t(item)}</span>)}</div></Panel><Panel icon={<GitBranch size={15} />} kicker="JSON CONTRACTS" title={t("仍需严格约束的字段")}><div className="json-contract-list">{data.storage_contract.json_fields.map((field) => <code key={field}>{field}</code>)}</div><p className="muted">{t("这些字段保留 JSON 的灵活性，但写入时必须经过 Pydantic/协议校验，不能让前端随意塞入任意结构。")}</p></Panel></div>}
      {section === "quality" && <Panel icon={<ShieldCheck size={15} />} kicker="READ ONLY · OPERATIONAL SOURCES" title={t("运行数据健康")} className="data-health-panel">
        {!data.health ? <p className="ops-source-note">{t("运行数据健康检查尚未提供；不能据此推断来源新鲜。")}</p> : <>
          <p className="ops-source-note">{t("检查生成")} {sourceTime(data.health.generated_at)} {t("· 页面读取")} {sourceTime(fetchedAt)} {t("· 下方时间来自采集或同步源，重新读取页面不会刷新来源。")}</p>
          <p className="data-catalog-boundary">{t(data.health.history_policy)}</p>
          <div className="quality-list">{data.health.checks.map((check) => { const retainedHistory = check.key === "session_history" && check.status === "info"; const historyCount = check.retained ?? check.total; return <article className="quality-row" key={check.key}>
            <div className={"quality-icon quality-icon--" + check.status}>{check.status === "good" ? <CheckCircle2 size={15} /> : <Wrench size={15} />}</div>
            <div className="quality-main"><div className="quality-head"><strong>{t(check.label)}</strong><span>{retainedHistory ? fmt(historyCount) + t(" 项历史来源保留") : check.total ? fmt(check.observed) + " / " + fmt(check.total) + t(" 项通过") : t("该来源未记录")}</span></div>
              <p>{t(check.detail)}{data.health!.thresholds_seconds[check.key] ? t(" 时效阈值 ") + data.health!.thresholds_seconds[check.key] / 60 + t(" 分钟。") : ""}</p>
              {check.items.length > 0 && <details><summary>{retainedHistory ? t("查看 ") + fmt(historyCount) + t(" 项历史来源") : t("查看 ") + (check.issue_count ?? check.items.length) + t(" 项待核对记录")}{check.truncated ? t("（仅展示前 100 项）") : ""}</summary>
                <ul className="data-health-items">{check.items.map((item, index) => <li key={item.kind + ":" + item.id + ":" + (item.runtime ?? "") + ":" + index}>
                  <code>{item.id}</code><span>{item.node ?? item.actor_id ?? item.kind}{item.runtime ? " · " + item.runtime : ""}</span><strong>{({ stale: t("来源过期"), unknown: t("时间未记录"), clock_skew: t("时钟待核对"), expired: t("租约到期"), incomplete: t("身份未完整登记"), duplicate_candidate: t("重复候选"), owner_unavailable: t("持棒身份待核对"), retained_history: t("历史来源（当前采集未配置）"), invalid_operator_configuration: t("历史来源配置无效") } as Record<string, string>)[item.state] ?? item.state}</strong>
                  <span>{check.key === "task_leases" ? t("到期时间 ") : t("来源采集/同步时间 ")}{sourceTime(item.observed_at)}</span>
                </li>)}</ul>
              </details>}
            </div>
          </article>; })}</div>
        </>}
      </Panel>}
    </div>
  );
}
