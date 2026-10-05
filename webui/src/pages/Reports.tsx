import { t, useI18n } from "../i18n";
import { useCallback, useMemo } from "react";
import { Coins } from "lucide-react";
import { api } from "../api";
import type { ActorInfo, MetricsSummary } from "../types";
import { DataState, Panel } from "../components/ui";
import { Avatar } from "../avatar";
import { exactTokens, formatTokens, measuredNumber, rangeLabel, sourceTime, useOperationsRead, type OperationsDays } from "../lib/operations";

export default function Reports({ days, actors }: { days: OperationsDays; actors: ActorInfo[] }) {
  useI18n();
  const reader = useCallback(() => api.get<MetricsSummary>("/api/metrics/summary?days=" + days + "&timezone=Asia%2FShanghai"), [days]);
  const { data: summary, readKey, fetchedAt, refreshing, error } = useOperationsRead(String(days), reader);
  const rows = useMemo(() => [...(summary?.actors ?? [])].sort((a, b) => (b.input ?? 0) + (b.output ?? 0) - (a.input ?? 0) - (a.output ?? 0)), [summary]);
  const reported = rows.filter((row) => row.usage_available !== false && (measuredNumber(row.input) !== null || measuredNumber(row.output) !== null));
  const knownInput = reported.map((row) => measuredNumber(row.input)).filter((value): value is number => value !== null);
  const knownOutput = reported.map((row) => measuredNumber(row.output)).filter((value): value is number => value !== null);
  const totalInput = knownInput.length ? knownInput.reduce((sum, value) => sum + value, 0) : null;
  const totalOutput = knownOutput.length ? knownOutput.reduce((sum, value) => sum + value, 0) : null;
  const max = Math.max(1, ...reported.map((row) => (row.input ?? 0) + (row.output ?? 0)));
  const name = (id: string) => actors.find((actor) => actor.id === id)?.display_name || id;
  const displayedDays = Number(readKey) || days;
  const sourceDates = reported.map((row) => row.last_reported_at).filter((value): value is string => !!value).sort();

  return <Panel icon={<Coins size={15} />} kicker="RETINUE · RUNTIME REPORTED USAGE" title={t("Retinue 模型响应用量")} className="reports-panel"
    tools={<span className="ops-source-note">{rangeLabel(displayedDays)} {t("· 已上报记录")}</span>}>
    {!summary && refreshing && <DataState loading />}
    {error && <DataState error={error} stale={!!summary} />}
    {summary && <>
      {readKey !== String(days) && <p className="ops-warning" role="status">{t("正在读取 {requested}；当前保留的是 {displayed} 的上次用量。", { requested: rangeLabel(days), displayed: rangeLabel(displayedDays) })}</p>}
      <p className="ops-source-note">{t("来源：Retinue 运行时累计上报 · 桶日期")} {summary.start} {t("至")} {summary.end ?? t("未记录")} {t("· 来源最近上报")} {sourceTime(sourceDates[sourceDates.length - 1])} {t("· 页面读取")} {sourceTime(fetchedAt)}</p>
      <p className="ops-source-note">{t("计量口径：运行时汇总，按登记身份归集。登记型号是当前名册配置；历史扫描可能覆盖多个实际模型，不能将汇总视为该登记型号的精确用量。现有用量记录未保存实际模型与来源身份的完整依据，保持未确认。")}</p>
      <div className="rt-mini-strip">
        <div><strong title={exactTokens(totalInput)}>{formatTokens(totalInput)}</strong><span>{t("已上报输入 Tokens")}</span></div>
        <div><strong title={exactTokens(totalOutput)}>{formatTokens(totalOutput)}</strong><span>{t("已上报输出 Tokens")}</span></div>
        <div><strong>{reported.length}</strong><span>{t("有用量记录的执行者")}</span></div>
      </div>
      {summary.coverage && <p className="ops-source-note">{t("覆盖 {reported} / {expected} 个执行者 · {missing} 个未上报 · 日桶覆盖{coverage}，原始计量完整性仍未确认", { reported: summary.coverage.reported_actors, expected: summary.coverage.expected_actors, missing: summary.coverage.missing_actors.length, coverage: summary.coverage.complete ? t("完整") : t("不完整") })}</p>}
      {reported.some((row) => row.stale) && <p className="ops-warning">{t("部分来源上报已过期；保留其历史计量，不能据此判断当前活动。")}</p>}
      <details className="ops-counting"><summary>{t("查看输入 token 计量口径与运行时分项")}</summary>
        <p className="ops-source-note">{t("Claude Code 的输入合计包含普通输入、缓存创建和缓存读取；Codex 的缓存输入已包含在输入总量中，不再重复相加。原始上报未存缓存拆分时，拆分保持未知。")}</p>
        {reported.flatMap((row) => (row.runtimes ?? []).map((runtime) => <p key={row.actor_id + ":" + runtime.runtime} className="ops-source-note" title={t("输入 ") + exactTokens(runtime.input) + t("；输出 ") + exactTokens(runtime.output)}>{name(row.actor_id)} · {runtime.runtime} {t("· 输入")} {formatTokens(runtime.input)} {t("/ 输出")} {formatTokens(runtime.output)} · {runtime.input_definition === "input + cache_creation_input + cache_read_input" ? t("输入含缓存创建与读取") : runtime.input_definition === "input (cached input is an included subset)" ? t("缓存输入为总输入子集") : t("上报输入；缓存拆分未记录")}</p>))}
      </details>
      <div className="usage-list">{rows.map((row) => {
        const available = row.usage_available !== false;
        const input = available ? measuredNumber(row.input) : null;
        const output = available ? measuredNumber(row.output) : null;
        return <div key={row.actor_id} className="usage-row">
          <span className="usage-name"><Avatar name={name(row.actor_id)} size={20} square />{name(row.actor_id)}
            <small>{t("运行时汇总 ·")} {row.runtimes?.map((runtime) => runtime.runtime).join(" / ") || t("运行时来源未记录")} · {row.stale ? t("上报过期") : row.last_reported_at ? t("已上报") : t("上报时间未记录")}</small>
            <small>{t("当前登记型号：")}{actors.find((actor) => actor.id === row.actor_id)?.model || t("未记录")} {t("· 不代表用量实际型号")}</small>
          </span>
          <div className="usage-bar" aria-label={name(row.actor_id) + t(" 已上报用量")}>
            {input !== null && <div className="usage-fill usage-input" style={{ width: input / max * 100 + "%" }} />}
            {output !== null && <div className="usage-fill usage-output" style={{ width: output / max * 100 + "%" }} />}
          </div>
          <span className="usage-value" title={t("输入 ") + exactTokens(input) + t("；输出 ") + exactTokens(output)}>{input === null && output === null ? t("未上报") : formatTokens(input) + " / " + formatTokens(output)}</span>
        </div>;
      })}</div>
      {reported.length === 0 && <DataState empty={t("该时段尚无可用模型用量上报；未知用量不能记为 0。")} />}
      <p className="disclaimer">{t("这里统计模型输入与输出 token 数，不是登录密钥 token。不同来源不会相加；未上报执行者不计作零用量，已上报合计不等于全体总消耗，也不等同于供应商账单。历史日桶未记录日界时区，不能精确换算到另一时区。")}</p>
    </>}
  </Panel>;
}
