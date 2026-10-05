import { t, useI18n } from "../i18n";
import { useCallback, useState } from "react";
import { BarChart3, RefreshCw, Trophy } from "lucide-react";
import { api } from "../api";
import type { ActorInfo, Throughput } from "../types";
import Reports from "./Reports";
import KingdomOperationsPage from "./KingdomOperationsPage";
import { Avatar } from "../avatar";
import { Ambient, DataState, PageHeader, Panel } from "../components/ui";
import { requestDataRefresh } from "../lib/refresh";
import { rangeLabel, sourceTime, useOperationsRead, type OperationsDays } from "../lib/operations";
import "./operations.css";

export default function Operations({ kingdomOn }: { kingdomOn: boolean }) {
  useI18n();
  const [days, setDays] = useState<OperationsDays>(7);
  const readThroughput = useCallback(() => api.get<Throughput>("/api/metrics/throughput?days=" + days + "&timezone=Asia%2FShanghai"), [days]);
  const throughput = useOperationsRead(String(days), readThroughput);
  const readActors = useCallback(() => api.get<ActorInfo[]>("/api/actors"), []);
  const actors = useOperationsRead("actors", readActors);
  const nameOf = (id: string) => actors.data?.find((actor) => actor.id === id)?.display_name || id;
  const chartDays = throughput.data?.days ?? [];
  const maxReceipts = Math.max(1, ...chartDays.map((day) => day.receipts));
  const displayedDays = Number(throughput.readKey) || days;

  return <div className="rt-page operations-page">
    <Ambient />
    <PageHeader kicker="OPERATIONS · SOURCE AWARE" title={t("运营看板")}
      subtitle={t("任务流转与模型响应用量按来源展示；完成标记与成果验收分别统计。")}
      tools={<><div className="rt-segmented" role="group" aria-label={t("运营统计时段")}>
        {([1, 7, 30] as const).map((value) => <button key={value} aria-pressed={days === value} className={days === value ? "is-active" : ""} onClick={() => setDays(value)}>{rangeLabel(value)}</button>)}
      </div><button className="rt-button rt-button--soft" onClick={requestDataRefresh}><RefreshCw size={15} />{t("刷新页面数据")}</button></>}
    />
    <p className="ops-source-note">{t("页面可见时每分钟重新读取。重新读取不会更新设备采集源；历史任务与已结束会话保留供复盘。")}</p>
    <section aria-label={t("Retinue 任务流转统计")}>
      <div className="ops-source-heading"><h2>{t("Retinue 任务流转")}</h2><span>{t("来源：任务事件链 ·")} {rangeLabel(displayedDays)}</span></div>
      {throughput.error && <DataState error={throughput.error} stale={!!throughput.data} />}
      {!throughput.data && throughput.refreshing && <DataState loading />}
      {throughput.data && <>
        {throughput.readKey !== String(days) && <p className="ops-warning" role="status">{t("正在读取 {requested}；当前保留的是 {displayed} 的上次结果。", { requested: rangeLabel(days), displayed: rangeLabel(displayedDays) })}</p>}
        <p className="ops-source-note">{t("页面读取于")} {sourceTime(throughput.fetchedAt)} {t("· 事件起始日")} {throughput.data.start} {t("· 日界时区")} {throughput.data.timezone ?? t("旧接口未记录")} {t("· 状态完成数不等同于已验收成果数")}</p>
        {throughput.data.diagnostics && (throughput.data.diagnostics.timezone_unknown_timestamps + throughput.data.diagnostics.invalid_timestamps + throughput.data.diagnostics.future_events_in_candidate_window > 0) && <p className="ops-warning">{t("统计候选窗口有")} {throughput.data.diagnostics.timezone_unknown_timestamps} {t("条历史事件的时区未知、")}{throughput.data.diagnostics.invalid_timestamps} {t("条时间无法解析、")}{throughput.data.diagnostics.future_events_in_candidate_window} {t("条时间在未来，未计入此图。原始任务事件与历史仍保留。")}</p>}
        {actors.error && <p className="ops-warning">{t("执行者名称读取失败，使用登记 ID 展示。")}</p>}
        <div className="rt-layout rt-layout--hero">
          <Panel icon={<BarChart3 size={15} />} kicker="TASK EVENT LEDGER" title={t("任务吞吐")}>
            {chartDays.length === 0 ? <p className="muted">{t("所选时段暂无任务事件记录。")}</p> : <>
              <div className="tp-chart">{chartDays.map((day) => <div key={day.date} className="tp-col" title={day.date + t(" · 事件 ") + day.receipts + t(" · 完成标记 ") + day.done}>
                <div className="tp-bars"><div className="tp-receipts" style={{ height: day.receipts / maxReceipts * 100 + "%" }} /><div className="tp-done" style={{ height: day.done / maxReceipts * 100 + "%" }} /></div><span>{day.date.slice(5)}</span>
              </div>)}</div><div className="dm-legend"><span><i style={{ background: "var(--blue)" }} /> {t("任务事件")}</span><span><i style={{ background: "var(--teal)" }} /> {t("完成标记")}</span></div>
            </>}
          </Panel>
          <Panel icon={<Trophy size={15} />} kicker="DONE TRANSITIONS" title={t("完成标记排行")}>
            <p className="ops-source-note">{t("按进入 done 的事件操作者统计，不能替代执行者贡献或成果验收。")}</p>
            <ol className="rank-list">{(throughput.data.done_by_actor ?? []).slice(0, 8).map((row, index) => <li key={row.actor_id}><span className="rank-index">{index + 1}</span><Avatar name={nameOf(row.actor_id)} size={24} square /><span className="rank-name">{nameOf(row.actor_id)}</span><strong>{row.done}</strong></li>)}</ol>
            {throughput.data.done_by_actor.length === 0 && <p className="muted">{t("所选时段暂无完成标记记录。")}</p>}
          </Panel>
        </div>
      </>}
    </section>
    <Reports days={days} actors={actors.data ?? []} />
    {kingdomOn && <KingdomOperationsPage days={days} />}
  </div>;
}
