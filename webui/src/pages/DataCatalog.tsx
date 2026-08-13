import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, Database, FileText, Gauge, GitBranch, LockKeyhole, RefreshCw, ShieldCheck, Sparkles, Table2, Wrench } from "lucide-react";
import { api, ApiError } from "../api";
import { Ambient, Metric, PageHeader, Panel } from "../components/ui";
import { useVocab } from "../theme";

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
}

const fmt = (value: number) => value.toLocaleString("zh-CN");
const date = (value: string) => new Date(value).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });

export default function DataCatalog() {
  const vocab = useVocab();
  const [data, setData] = useState<DataCatalogInfo | null>(null);
  const [error, setError] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [section, setSection] = useState<"layers" | "quality" | "contract">("layers");

  const load = useCallback(async () => {
    try {
      setData(await api.get<DataCatalogInfo>("/api/data-catalog"));
      setError("");
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "无法读取数据目录");
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const refresh = async () => {
    setRefreshing(true);
    await load();
    window.setTimeout(() => setRefreshing(false), 180);
  };

  if (!data && !error) return <div className="rt-loading"><Database size={25} />{vocab.dataCatalogLoading}</div>;
  if (!data) return <div className="rt-empty-state"><Database size={25} /><strong>数据目录暂不可用</strong><span>{error}</span><button className="rt-button rt-button--primary" onClick={() => void load()}>重试</button></div>;

  const summary = data.summary;
  return (
    <div className="rt-page data-catalog-page">
      <Ambient />
      <PageHeader
        kicker="RETINUE · DATA WORKBENCH"
        title="数据整理台"
        subtitle={`把数据库、Obsidian 和智能体注册表放进同一张可解释的地图 · 最近更新 ${date(data.generated_at)}`}
        tools={<button className="rt-button rt-button--soft" disabled={refreshing} onClick={() => void refresh()}><RefreshCw size={15} className={refreshing ? "rt-spin" : ""} />{refreshing ? "刷新中" : "刷新目录"}</button>}
      />

      <div className="data-catalog-tabs" role="tablist" aria-label="数据整理台视图">
        {[['layers', '数据层', <Database size={14} />], ['quality', '质量检查', <Gauge size={14} />], ['contract', '格式与边界', <ShieldCheck size={14} />]].map(([key, label, icon]) => <button key={String(key)} role="tab" aria-selected={section === key} className={section === key ? "is-active" : ""} onClick={() => setSection(key as typeof section)}>{icon}<span>{label}</span></button>)}
        <span className="data-catalog-version">{data.schema_version}</span>
      </div>

      <div className="rt-metrics data-catalog-metrics">
        <Metric icon={<Table2 size={16} />} label="任务卡" value={fmt(summary.tasks)} sub={`${fmt(summary.events)} 条事件链`} tone="blue" />
        <Metric icon={<Sparkles size={16} />} label="技能注册" value={fmt(summary.skills)} sub={`${fmt(summary.actors)} 个成员`} tone="amber" />
        <Metric icon={<GitBranch size={16} />} label="知识与节点" value={`${summary.knowledge_sources} / ${summary.nodes}`} sub="知识源 / 基础设施" tone="teal" />
        <Metric icon={<CheckCircle2 size={16} />} label="数据质量" value={`${summary.quality_score}%`} sub={`${summary.pipeline_templates} 条流程模板`} tone={summary.quality_score >= 80 ? "green" : "amber"} />
      </div>

      {section === "layers" && <>
        <Panel icon={<Database size={15} />} kicker="STORAGE LAYERS" title={vocab.dataLayerTitle} tools={<span className="data-catalog-panel-note">只展示目录与字段，不读取原始正文</span>}>
          <div className="data-layer-list">{data.layers.map((layer) => <article className="data-layer-row" key={layer.key}><div className="data-layer-icon"><Database size={16} /></div><div className="data-layer-main"><div className="data-layer-title"><strong>{layer.title}</strong><span className={`data-status data-status--${layer.status}`}>{layer.status === "good" ? "已纳入" : layer.status === "attention" ? "待整理" : "信息层"}</span></div><span className="data-layer-table">{layer.table}</span><div className="data-field-list">{layer.fields.map((field) => <code key={field}>{field}</code>)}</div></div><div className="data-layer-count"><strong>{fmt(layer.rows)}</strong><span>记录</span></div><div className={`data-readonly-toggle ${layer.status !== "attention" ? "is-on" : ""}`} role="img" aria-label={layer.status !== "attention" ? "已纳入目录" : "待整理"}><i /></div></article>)}</div>
          <div className="data-catalog-boundary"><LockKeyhole size={15} /><span>{vocab.dataBoundary}</span></div>
        </Panel>
        <div className="data-catalog-grid-two"><Panel icon={<Wrench size={15} />} kicker="NEXT REFINEMENT" title="建议整理动作"><div className="data-recommendations">{data.recommendations.map((item, index) => <div key={item}><span>{index + 1}</span><p>{item}</p></div>)}</div></Panel><Panel icon={<FileText size={15} />} kicker="DOCUMENT PIPELINE" title="文件流转"><div className="data-flow"><span className="data-flow-step data-flow-step--input">输入</span><b>→</b><span className="data-flow-step data-flow-step--clean">清洗</span><b>→</b><span className="data-flow-step data-flow-step--index">索引</span><b>→</b><span className="data-flow-step data-flow-step--output">输出</span></div><p className="muted">原始纪要先进 Inbox；确认后的结构化 Markdown 才进入知识库，回答和交付物通过双链回指来源。</p></Panel></div>
      </>}

      {section === "quality" && <Panel icon={<Gauge size={15} />} kicker="DATA QUALITY GATE" title="字段质量与结果指标" tools={<span className="data-score">当前得分 {data.quality.score}%</span>}><div className="quality-list">{data.quality.checks.map((check) => { const ratio = check.total ? Math.round(check.observed / check.total * 100) : 100; return <article className="quality-row" key={check.key}><div className={`quality-icon quality-icon--${check.status}`}>{check.status === "good" ? <CheckCircle2 size={15} /> : <Wrench size={15} />}</div><div className="quality-main"><div className="quality-head"><strong>{check.label}</strong><span>{check.total ? `${fmt(check.observed)} / ${fmt(check.total)}` : "暂无记录"}</span></div><div className="quality-bar"><i style={{ width: `${Math.max(4, ratio)}%` }} /></div><p>{check.detail}</p></div></article>; })}</div><div className="data-catalog-boundary"><ShieldCheck size={15} /><span>结果指标先于“完成”标签：任务完成率、验收条件覆盖率、事件链完整率和数据新鲜度都应可查询。</span></div></Panel>}

      {section === "contract" && <div className="data-catalog-grid-two"><Panel icon={<FileText size={15} />} kicker="CANONICAL FORMAT" title="格式分工"><dl className="contract-list"><div><dt>纪要与知识</dt><dd>{data.storage_contract.documents}</dd></div><div><dt>任务与运行</dt><dd>{data.storage_contract.operational}</dd></div><div><dt>当前真相源</dt><dd>{data.storage_contract.canonical}</dd></div></dl></Panel><Panel icon={<LockKeyhole size={15} />} kicker="PRIVACY BOUNDARY" title="网页可见边界"><p className="data-catalog-privacy">{data.privacy.web_catalog}</p><div className="privacy-excluded">{data.privacy.excluded.map((item) => <span key={item}>不展示 · {item}</span>)}</div></Panel><Panel icon={<GitBranch size={15} />} kicker="JSON CONTRACTS" title="仍需严格约束的字段"><div className="json-contract-list">{data.storage_contract.json_fields.map((field) => <code key={field}>{field}</code>)}</div><p className="muted">这些字段保留 JSON 的灵活性，但写入时必须经过 Pydantic/协议校验，不能让前端随意塞入任意结构。</p></Panel></div>}
    </div>
  );
}