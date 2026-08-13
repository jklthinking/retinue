import { useEffect, useMemo, useState } from "react";
import { Coins } from "lucide-react";
import { api, readErrorMessage } from "../api";
import type { ActorInfo, MetricsSummary } from "../types";
import { DataState, Panel } from "../components/ui";
import { Avatar } from "../avatar";

function formatTokens(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
  return String(value);
}

export default function Reports() {
  const [days, setDays] = useState(7);
  const [summary, setSummary] = useState<MetricsSummary | null>(null);
  const [actors, setActors] = useState<ActorInfo[]>([]);
  const [loadingSummary, setLoadingSummary] = useState(true);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [loadingActors, setLoadingActors] = useState(true);
  const [actorsError, setActorsError] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      setLoadingSummary(true);
      try {
        setSummary(await api.get<MetricsSummary>(`/api/metrics/summary?days=${days}`));
        setSummaryError(null);
      } catch (reason) {
        setSummaryError(readErrorMessage(reason));
      } finally {
        setLoadingSummary(false);
      }
    })();
  }, [days]);

  useEffect(() => {
    void (async () => {
      try {
        setActors(await api.get<ActorInfo[]>("/api/actors"));
        setActorsError(null);
      } catch (reason) {
        setActorsError(readErrorMessage(reason));
      } finally {
        setLoadingActors(false);
      }
    })();
  }, []);

  const rows = useMemo(() => {
    if (!summary) return [];
    return [...summary.actors].sort((a, b) => b.input + b.output - (a.input + a.output));
  }, [summary]);

  const max = rows.length ? rows[0].input + rows[0].output : 1;
  const name = (id: string) => actors.find((a) => a.id === id)?.display_name || id;
  const totalInput = rows.reduce((sum, row) => sum + row.input, 0);
  const totalOutput = rows.reduce((sum, row) => sum + row.output, 0);

  return (
    <Panel
      icon={<Coins size={15} />}
      kicker="TOKEN USAGE · DE-IDENTIFIED"
      title="Token 用量"
      className="reports-panel"
      tools={
        <div className="rt-segmented">
          {[
            [1, "今天"],
            [7, "近 7 日"],
            [30, "近 30 日"],
          ].map(([d, label]) => (
            <button
              key={d}
              className={days === d ? "is-active" : ""}
              onClick={() => setDays(d as number)}
            >
              {label}
            </button>
          ))}
        </div>
      }
    >
      {(loadingSummary || loadingActors) && <DataState loading />}
      {summaryError && <DataState error={summaryError} />}
      {actorsError && <DataState error={`执行者名称读取失败：${actorsError}`} />}
      {!loadingSummary && !summaryError && !loadingActors && !actorsError && <>
      <div className="rt-mini-strip">
        <div>
          <strong>{formatTokens(totalInput)}</strong>
          <span>输入 Tokens</span>
        </div>
        <div>
          <strong>{formatTokens(totalOutput)}</strong>
          <span>输出 Tokens</span>
        </div>
        <div>
          <strong>{rows.length}</strong>
          <span>活跃执行者</span>
        </div>
      </div>
      <div className="usage-list">
        {rows.map((row) => (
          <div key={row.actor_id} className="usage-row">
            <span className="usage-name">
              <Avatar name={name(row.actor_id)} size={20} square />
              {name(row.actor_id)}
            </span>
            <div className="usage-bar">
              <div
                className="usage-fill usage-input"
                style={{ width: `${(row.input / max) * 100}%` }}
              />
              <div
                className="usage-fill usage-output"
                style={{ width: `${(row.output / max) * 100}%` }}
              />
            </div>
            <span className="usage-value">
              {formatTokens(row.input)} / {formatTokens(row.output)}
            </span>
          </div>
        ))}
        {rows.length === 0 && <DataState empty="该时段确实暂无用量记录。" />}
      </div>
      <p className="disclaimer">
        用量为各运行时上报的脱敏统计值(仅 token 计数,不含会话正文、提示词或密钥),用于内部投入产出比较,不等同于供应商账单。
      </p>
      </>}
    </Panel>
  );
}
