import { useCallback, useState } from "react";
import { Activity, BarChart3, Bot, Clock3, RefreshCw, ShieldCheck, Sparkles, Users } from "lucide-react";
import { fetchJSON } from "@/lib/api";
import { kingdomApi, type KingdomNodeId } from "@/lib/kingdom";
import { DataState } from "../components/ui";
import { exactTokens, formatTokens, measuredNumber, sourceTime, useOperationsRead, type OperationsDays } from "../lib/operations";
import { requestDataRefresh } from "../lib/refresh";
import { gatewayProcessState, observationState, operationsCalendarDay } from "../lib/observation";
import "./operations.css";

interface UsagePoint { tokens: number | null; sessions: number }
interface HourPoint extends UsagePoint { hour: number }
interface DayPoint extends UsagePoint { day: string }
interface OperationsAgent {
  id: string; node: KingdomNodeId; display_name: string; role: string; kind: string; model: string; provider: string;
  observed_at?: string | null; snapshot_stale?: boolean;
  gateway: { healthy: boolean; active: string; sub: string; current_process_healthy?: boolean | null; observed_at?: string | null; snapshot_stale?: boolean };
  sessions: number; messages: number;
  usage: {
    today_input: number; today_output: number; today_tokens: number; today_sessions: number; week_tokens: number;
    current_today_input?: number | null; current_today_output?: number | null; current_today_tokens?: number | null;
    available?: boolean; availability?: string; observed_at?: string | null; stat_date?: string | null;
    window_current?: boolean; snapshot_stale?: boolean; completeness?: string;
    last_active_at?: number | string | null; hourly: HourPoint[]; daily: DayPoint[];
  };
}
interface OperationsResponse {
  schema_version: number; generated_at: string; source: string; agents: OperationsAgent[];
  nodes: Array<{ id: KingdomNodeId; freshness_seconds: number | null; stale: boolean; snapshot_at?: string | null; generated_at?: string | null }>;
  totals: {
    agents: number; online_agents: number; active_today: number; today_input: number; today_output: number; today_tokens: number;
    today_sessions: number; open_tasks: number; all_tasks: number;
    current_today_input?: number | null; current_today_output?: number | null; current_today_tokens?: number | null;
    usage_available?: boolean; available_agents?: number; missing_agents?: number; process_healthy_gateways?: number | null; current_process_healthy_gateways?: number | null;
  };
}
const COLORS = ["#2563EB", "#1FA593", "#C9962B", "#D95C62", "#0B1D3A", "#8BB8B0"];
type Range = "today" | "week";
const availableUsage = (agent: OperationsAgent) => agent.usage.available === true;
const snapshotValue = (agent: OperationsAgent, range: Range) => availableUsage(agent)
  ? measuredNumber(range === "today" ? agent.usage.today_tokens : agent.usage.week_tokens) : null;
const sumKnown = (values: (number | null)[]) => values.some((value) => value !== null)
  ? values.reduce<number>((sum, value) => sum + (value ?? 0), 0) : null;

function Metric({ label, value, meta, icon, tone, exactValue }: { label: string; value: string; meta: string; icon: React.ReactNode; tone: string; exactValue?: string }) {
  return <article className={"ko-metric ko-metric--" + tone}><div className="ko-metric__icon">{icon}</div><div><span>{label}</span><strong title={exactValue}>{value}</strong><small>{meta}</small></div></article>;
}
function Donut({ agents, range }: { agents: OperationsAgent[]; range: Range }) {
  const values = agents.map((agent) => snapshotValue(agent, range));
  const total = sumKnown(values);
  let cursor = 0;
  const stops = values.map((value, index) => {
    const start = cursor; cursor += total && value !== null ? value / total * 100 : 0;
    return COLORS[index % COLORS.length] + " " + start + "% " + cursor + "%";
  });
  const background = total ? "conic-gradient(" + stops.join(",") + ")" : "conic-gradient(#e9edf3 0 100%)";
  return <div className="ko-consumption"><div className="ko-donut" style={{ background }}><div><strong title={exactTokens(total)}>{formatTokens(total)}</strong><span>来源快照记录</span></div></div>
    <div className="ko-legend"><header><span>Hermes Profile</span><span>模型 Tokens</span><span>占比</span></header>
      {agents.map((agent, index) => <div key={agent.id}><span><i style={{ background: COLORS[index % COLORS.length] }} />{agent.display_name}</span>
        <strong title={exactTokens(values[index])}>{formatTokens(values[index])}</strong><em>{total && values[index] !== null ? Math.round(values[index]! / total * 100) + "%" : "—"}</em></div>)}
    </div>
  </div>;
}
function Trend({ agents, range }: { agents: OperationsAgent[]; range: Range }) {
  const labels = range === "today" ? Array.from({ length: 24 }, (_, hour) => String(hour).padStart(2, "0") + ":00")
    : [...new Set(agents.flatMap((agent) => (agent.usage.daily ?? []).map((point) => point.day)))].sort();
  const values = agents.map((agent) => labels.map((label, index) => !availableUsage(agent) ? null : measuredNumber(range === "today"
    ? agent.usage.hourly?.find((point) => point.hour === index)?.tokens
    : agent.usage.daily?.find((point) => point.day === label)?.tokens)));
  const known = values.flat().filter((value): value is number => value !== null);
  const max = Math.max(1, ...known);
  const width = 760, height = 260, left = 54, right = 18, top = 18, bottom = 38;
  const plotWidth = width - left - right, plotHeight = height - top - bottom;
  const x = (index: number) => left + (labels.length <= 1 ? 0 : index / (labels.length - 1) * plotWidth);
  const y = (value: number) => top + plotHeight - value / max * plotHeight;
  return <div className="ko-trend">
    {known.length === 0 ? <p className="ops-source-note">暂无可用模型 token 趋势；缺失时间点不补零。</p> : <svg viewBox={"0 0 " + width + " " + height} role="img" aria-label="Hermes 快照模型 Token 趋势">
      {[0, .25, .5, .75, 1].map((ratio) => <g key={ratio}><line x1={left} y1={top + plotHeight * ratio} x2={width - right} y2={top + plotHeight * ratio} /><text x={left - 8} y={top + plotHeight * ratio + 4} textAnchor="end">{formatTokens(Math.round(max * (1 - ratio)))}</text></g>)}
      {labels.map((label, index) => range === "week" || index % 3 === 1 || index === labels.length - 1 ? <text key={label} className="ko-axis-label" x={x(index)} y={height - 11} textAnchor="middle">{range === "week" ? label.slice(5) : label}</text> : null)}
      {values.map((series, seriesIndex) => {
        // Separate continuous segments: a missing observation is a gap, not zero.
        const segments: string[][] = []; let current: string[] = [];
        series.forEach((value, index) => { if (value === null) { if (current.length) segments.push(current); current = []; } else current.push(x(index) + "," + y(value)); });
        if (current.length) segments.push(current);
        return <g key={agents[seriesIndex].id}>{segments.map((points, index) => <polyline key={index} points={points.join(" ")} style={{ stroke: COLORS[seriesIndex % COLORS.length] }} />)}
          {series.map((value, index) => value !== null ? <circle key={index} cx={x(index)} cy={y(value)} r="3" style={{ fill: COLORS[seriesIndex % COLORS.length] }}><title>{agents[seriesIndex].display_name + " · " + labels[index] + " · " + exactTokens(value)}</title></circle> : null)}</g>;
      })}
    </svg>}
    <div className="ko-trend__legend">{agents.map((agent, index) => <span key={agent.id}><i style={{ background: COLORS[index % COLORS.length] }} />{agent.display_name}</span>)}</div>
  </div>;
}
function AgentGrid({ agents, range, nodes }: { agents: OperationsAgent[]; range: Range; nodes: OperationsResponse["nodes"] }) {
  const values = agents.map((agent) => snapshotValue(agent, range));
  const max = Math.max(...values.map((value) => value ?? 0), 1);
  return <div className="ko-agent-grid">{agents.map((agent, index) => {
    const value = values[index];
    const node = nodes.find((node) => node.id === agent.node);
    const stale = agent.snapshot_stale || node?.stale || observationState(agent.usage.observed_at ?? agent.observed_at ?? node?.snapshot_at ?? node?.generated_at) !== "fresh";
    const healthy = gatewayProcessState(agent.gateway, nodes.find((node) => node.id === agent.node), agent);
    return <article className="ko-agent" key={agent.id}><header><div className={"ko-avatar ko-avatar--" + agent.kind}>{agent.display_name.slice(0, 1)}</div>
      <div><strong>{agent.display_name}</strong><span>{agent.role}</span></div>
      <em className={healthy === true ? "is-online" : healthy === false ? "is-offline" : "is-unknown"}>{healthy === true ? "Gateway 运行" : healthy === false ? "Gateway 停止" : "Gateway 未确认"}</em></header>
      <div className="ko-agent__model"><Bot size={13} />{agent.model || "模型未记录"}<span>{agent.node}</span></div>
      <div className="ko-agent__usage"><span>{range === "today" ? "快照日模型 Tokens" : "快照 7 日模型 Tokens"}</span><strong title={exactTokens(value)}>{formatTokens(value)}</strong></div>
      <p className="ops-source-note">快照日 {agent.usage.stat_date ?? agent.usage.daily?.[agent.usage.daily.length - 1]?.day ?? "未记录"} · {stale ? "来源过期" : "来源新鲜度按采集时间"}</p>
      <footer><span>{agent.usage.today_sessions} 个快照日会话</span><b>{value === null ? "未计量" : Math.round(value / max * 100) + "%"}</b></footer>
      <p className="ops-source-note">最后活动 {sourceTime(agent.usage.last_active_at)} · 采集 {sourceTime(agent.usage.observed_at ?? agent.observed_at)}</p>
      <div className="ko-progress">{value !== null && <i style={{ width: value / max * 100 + "%", background: COLORS[index % COLORS.length] }} />}</div>
    </article>;
  })}</div>;
}

export default function KingdomOperationsPage({ days = 7 }: { days?: OperationsDays }) {
  const reader = useCallback(() => fetchJSON<OperationsResponse>("/api/kingdom/operations", { cache: "no-store" }, { timeoutMs: 12_000 }), []);
  const { data, fetchedAt, refreshing, error } = useOperationsRead("hermes", reader);
  const [syncing, setSyncing] = useState(false);
  const [syncError, setSyncError] = useState("");
  const range: Range = days === 1 ? "today" : "week";
  const sync = async () => {
    setSyncing(true);
    try { await kingdomApi.refresh(); setSyncError(""); requestDataRefresh(); }
    catch (reason) { setSyncError(reason instanceof Error ? reason.message : "来源同步失败"); }
    finally { setSyncing(false); }
  };
  if (!data) return <section aria-label="Hermes 会话来源"><h2>Hermes 会话来源</h2>{error ? <DataState error={error} onRetry={requestDataRefresh} /> : <DataState loading={refreshing} />}</section>;
  const t = data.totals;
  const sourceStates = data.nodes.map((node) => node.stale ? "stale" : observationState(node.snapshot_at ?? node.generated_at));
  const stale = sourceStates.length === 0 || sourceStates.some((state) => state !== "fresh");
  const readFailed = !!error || !!syncError;
  const sourceCurrent = !stale && data.agents.length > 0 && data.agents.every((agent) => !agent.snapshot_stale && observationState(agent.observed_at ?? data.nodes.find((node) => node.id === agent.node)?.generated_at) === "fresh");
  const gatewayCurrent = sourceCurrent && data.agents.every((agent) => gatewayProcessState(agent.gateway, data.nodes.find((node) => node.id === agent.node), agent) !== null);
  const calendarCurrent = data.agents.length > 0 && data.agents.every((agent) => (agent.usage.stat_date ?? agent.usage.daily?.[agent.usage.daily.length - 1]?.day) === operationsCalendarDay());
  const currentUsage = sourceCurrent && calendarCurrent && !readFailed;
  const processHealthy = gatewayCurrent && !readFailed ? measuredNumber(t.current_process_healthy_gateways) : null;
  const today = currentUsage ? measuredNumber(t.current_today_tokens) : null;
  const todayInput = currentUsage ? measuredNumber(t.current_today_input) : null;
  const todayOutput = currentUsage ? measuredNumber(t.current_today_output) : null;
  const total = sumKnown(data.agents.map((agent) => snapshotValue(agent, range)));
  const statDates = [...new Set(data.agents.map((agent) => agent.usage.stat_date ?? agent.usage.daily?.[agent.usage.daily.length - 1]?.day).filter(Boolean))].join(" / ");
  return <section className="ko-page ko-page--embedded" aria-label="Hermes 会话来源">
    <header className="ko-header"><div className="ko-heading"><div className="ko-mark"><BarChart3 size={23} /></div><div>
      <span>HERMES · SESSION METADATA</span><h2>Hermes 会话来源</h2><p>按会话来源单独统计，不能与 Retinue 上报用量相加。Gateway 进程状态不代表 worker 可接任务。</p>
    </div></div><button className="ko-refresh" disabled={syncing} onClick={() => void sync()}><RefreshCw className={syncing ? "is-spinning" : ""} size={15} />{syncing ? "同步来源中" : "同步 Hermes 来源"}</button></header>
    {error && <DataState error={error} stale />}
    {syncError && <DataState error={syncError} stale />}
    {days === 30 && <p className="ops-warning">Hermes 当前仅提供来源快照中的 7 日记录；无法给出 30 日用量，下方保留可用的 7 日历史图。</p>}
    {stale && <p className="ops-warning">来源快照已过期；历史会话记录继续保留，当前用量与 Gateway 状态未确认。</p>}
    <div className="ops-source-meta">
      {data.nodes.map((node, index) => <span key={node.id}>{node.id} 采集 {sourceTime(node.snapshot_at ?? node.generated_at)} · {({ fresh: "按来源阈值新鲜", stale: "来源过期", unknown: "来源时间未记录", clock_skew: "时钟待核对" })[sourceStates[index]]}</span>)}
      <span>投影生成 {sourceTime(data.generated_at)} · 页面读取 {sourceTime(fetchedAt)}</span>
    </div>
    <main className="ko-content">
      <section className="ko-metrics">
        <Metric label="Gateway 进程正常" value={processHealthy === null ? "未知" : processHealthy + "/" + t.agents} meta="新鲜进程快照；不是 worker 在线数" icon={<Bot />} tone="green" />
        <Metric label={range === "today" ? "当前统计日会话记录 Tokens" : "快照 7 日模型 Tokens"} value={formatTokens(range === "today" ? today : total)} exactValue={exactTokens(range === "today" ? today : total) + (range === "today" ? "；输入 " + exactTokens(todayInput) + "；输出 " + exactTokens(todayOutput) : "")} meta={range === "today" ? "输入 " + formatTokens(todayInput) + " · 输出 " + formatTokens(todayOutput) : "来源记录合计 · 完整性未确认"} icon={<Sparkles />} tone="blue" />
        <Metric label="快照日有新会话" value={t.active_today + "/" + t.agents} meta={t.today_sessions + " 个快照日新会话 · " + (statDates || "日期未记录")} icon={<Activity />} tone="amber" />
        <Metric label="Hermes 进行中任务" value={String(t.open_tasks)} meta={"来源历史任务总量 " + t.all_tasks} icon={<Clock3 />} tone="purple" />
      </section>
      <p className="ops-source-note">统计日 {statDates || "未记录"} · 可计量 Profile {t.available_agents ?? data.agents.filter(availableUsage).length} / {t.agents} · 来源日界时区未记录，会话开始日不等于 token 实际发生时间。</p>
      <section className="ko-chart-grid">
        <article className="ko-panel"><header><div><h3>来源快照模型 Token 分布</h3><span>{range === "today" ? "快照日记录" : "快照中的 7 日记录"} · 未计量不补零</span></div><Users size={18} /></header><Donut agents={data.agents} range={range} /></article>
        <article className="ko-panel"><header><div><h3>来源快照模型 Token 趋势</h3><span>{range === "today" ? "快照日小时桶" : "来源记录的日期桶"} · 时区未记录</span></div><BarChart3 size={18} /></header><Trend agents={data.agents} range={range} /></article>
      </section>
      <section className="ko-panel ko-panel--agents"><header><div><h3>Hermes Profile 与历史活动</h3><span>配置模型、Gateway 状态与模型计量分别展示</span></div></header><AgentGrid agents={data.agents} range={range} nodes={data.nodes} /></section>
      <div className="ko-disclaimer"><ShieldCheck size={15} /><span>模型输入/输出 token 数来自 Hermes 会话数据库，不是鉴权密钥 token。原始记录完整性未知，缺失计量显示未知；记录值不等同于供应商账单，不读取会话正文、提示词或密钥。</span></div>
    </main>
  </section>;
}
