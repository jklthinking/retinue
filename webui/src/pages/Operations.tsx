import { useEffect, useState } from "react";
import { BarChart3, Trophy } from "lucide-react";
import { api, readErrorMessage } from "../api";
import type { ActorInfo, Throughput } from "../types";
import Reports from "./Reports";
import { Avatar } from "../avatar";
import { Ambient, DataState, PageHeader, Panel } from "../components/ui";

export default function Operations() {
  const [throughput, setThroughput] = useState<Throughput | null>(null);
  const [actors, setActors] = useState<ActorInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        const [throughputInfo, actorRows] = await Promise.all([
          api.get<Throughput>("/api/metrics/throughput?days=14"),
          api.get<ActorInfo[]>("/api/actors"),
        ]);
        setThroughput(throughputInfo);
        setActors(actorRows);
        setError(null);
      } catch (reason) {
        setError(readErrorMessage(reason));
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const nameOf = (id: string) => actors.find((a) => a.id === id)?.display_name || id;
  const days = throughput?.days ?? [];
  const maxReceipts = Math.max(1, ...days.map((d) => d.receipts));

  return (
    <div className="rt-page operations-page">
      <Ambient />
      <PageHeader
        kicker="OPERATIONS · GLOBAL"
        title="运营看板"
        subtitle="任务吞吐、完成排行与脱敏 Token 投入,一处看清产能。"
      />

      {loading && <DataState loading />}
      {error && <DataState error={error} />}

      {!loading && !error && <>
      <div className="rt-layout rt-layout--hero">
        <Panel icon={<BarChart3 size={15} />} kicker="THROUGHPUT · 14D" title="任务吞吐">
          {days.length === 0 ? (
            <p className="muted">近 14 天确实没有任务事件</p>
          ) : (
            <>
              <div className="tp-chart">
                {days.map((day) => (
                  <div key={day.date} className="tp-col" title={`${day.date} · 回执 ${day.receipts} · 完成 ${day.done}`}>
                    <div className="tp-bars">
                      <div
                        className="tp-receipts"
                        style={{ height: `${(day.receipts / maxReceipts) * 100}%` }}
                      />
                      <div
                        className="tp-done"
                        style={{ height: `${(day.done / maxReceipts) * 100}%` }}
                      />
                    </div>
                    <span>{day.date.slice(5)}</span>
                  </div>
                ))}
              </div>
              <div className="dm-legend">
                <span>
                  <i style={{ background: "var(--blue)" }} /> 回执
                </span>
                <span>
                  <i style={{ background: "var(--teal)" }} /> 完成
                </span>
              </div>
            </>
          )}
        </Panel>

        <Panel icon={<Trophy size={15} />} kicker="DONE BY AGENT" title="完成排行">
          <ol className="rank-list">
            {(throughput?.done_by_actor ?? []).slice(0, 8).map((row, index) => (
              <li key={row.actor_id}>
                <span className="rank-index">{index + 1}</span>
                <Avatar name={nameOf(row.actor_id)} size={24} square />
                <span className="rank-name">{nameOf(row.actor_id)}</span>
                <strong>{row.done}</strong>
              </li>
            ))}
            {(throughput?.done_by_actor ?? []).length === 0 && (
              <p className="muted">时段内确实没有完成记录</p>
            )}
          </ol>
        </Panel>
      </div>

      <Reports />
      </>}
    </div>
  );
}
