import { useCallback, useEffect, useRef, useState } from "react";
import { Gauge } from "lucide-react";
import { t, useI18n } from "../i18n";
import { readErrorMessage } from "../api";
import { panelNow } from "../demo";
import {
  fetchQuota,
  formatBalance,
  formatFetchedAgo,
  formatUsedPercent,
  formatResetLine,
  formatShanghaiDateTime,
  isVisibleQuotaProvider,
  progressTone,
  providerLabel,
  sortQuotaProviders,
  windowLabel,
  type QuotaProviderEntry,
  type QuotaResponse,
  type QuotaWindow,
} from "../lib/quota";
import { BOARD_REFRESH_MS, DATA_REFRESH_EVENT } from "../lib/refresh";
import { DataState, Panel } from "./ui";
import "./quota-panel.css";

const EMPTY_HINT =
  "还没有额度数据。在节点上运行 retinue-node quota --enable … 开启。";

function WindowRows({
  windows,
  now,
  muted = false,
}: {
  windows: QuotaWindow[];
  now: number;
  muted?: boolean;
}) {
  return (
    <>
      {windows.map((window) => {
        const tone = progressTone(window.used_percent);
        const percentText = formatUsedPercent(window.used_percent);
        return (
          <div
            key={window.key}
            className={`rt-quota-window${muted ? " rt-quota-window--muted" : ""}`}
          >
            <span className="rt-quota-window__label">{windowLabel(window)}</span>
            <div className="rt-quota-window__bar" aria-hidden={window.used_percent == null}>
              {window.used_percent != null && (
                <div className="rt-progress">
                  <span className={tone} style={{ width: `${Math.min(100, window.used_percent)}%` }} />
                </div>
              )}
            </div>
            <span className="rt-quota-window__percent">{percentText}</span>
            <span className="rt-quota-window__reset">{formatResetLine(window.resets_at, now)}</span>
          </div>
        );
      })}
    </>
  );
}

function accountOrdinal(entries: QuotaProviderEntry[], entry: QuotaProviderEntry): number | null {
  const same = entries.filter((item) => item.provider === entry.provider);
  return same.length > 1 ? same.indexOf(entry) + 1 : null;
}

function ProviderCard({ entry, now, ordinal }: { entry: QuotaProviderEntry; now: number; ordinal: number | null }) {
  // Account fingerprints are not shown; a second account of the same vendor gets an ordinal instead.
  const accountTag = ordinal ? t("账号 {number}", { number: ordinal }) : null;
  const title = providerLabel(entry.provider);
  const plan = entry.plan?.trim();
  const showLastOk =
    (entry.status === "error" || entry.status === "expired") &&
    entry.last_ok &&
    entry.last_ok.windows.length > 0;
  const balanceText = entry.kind === "api" ? formatBalance(entry.balance) : null;

  return (
    <article className="rt-quota-card" aria-label={t("{provider} 额度", { provider: title })}>
      <header className="rt-quota-card__head">
        <div className="rt-quota-card__title">
          <strong>{title}</strong>
          {accountTag && <em>{accountTag}</em>}
          {plan && <em>{plan}</em>}
        </div>
        <div className="rt-quota-card__meta">
          <span>{formatFetchedAgo(entry.fetched_at, now)}</span>
          {entry.stale && <span className="rt-quota-card__stale">{t("数据过期")}</span>}
        </div>
      </header>
      {(entry.status === "error" || entry.status === "expired") && entry.error && (
        <p className="rt-quota-card__error" role="alert">{entry.error}</p>
      )}
      {balanceText && <p className="rt-quota-card__balance">{balanceText}</p>}
      <WindowRows windows={entry.windows} now={now} />
      {showLastOk && entry.last_ok && (
        <section className="rt-quota-last-ok" aria-label={t("上次成功数据")}>
          <header>{t("上次数据（{time}）", { time: formatShanghaiDateTime(entry.last_ok.fetched_at) })}</header>
          <WindowRows windows={entry.last_ok.windows} now={now} muted />
        </section>
      )}
    </article>
  );
}

export default function QuotaPanel() {
  useI18n();
  const [payload, setPayload] = useState<QuotaResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loaded, setLoaded] = useState(false);
  const [tick, setTick] = useState(0);
  const seq = useRef(0);

  const reload = useCallback(() => {
    const ticket = ++seq.current;
    setLoading(true);
    void fetchQuota()
      .then((value) => {
        if (ticket !== seq.current) return;
        setPayload(value);
        setError(null);
        setLoaded(true);
      })
      .catch((reason) => {
        if (ticket !== seq.current) return;
        setError(readErrorMessage(reason));
      })
      .finally(() => {
        if (ticket === seq.current) setLoading(false);
      });
  }, []);

  useEffect(() => {
    reload();
    const timer = setInterval(reload, BOARD_REFRESH_MS);
    const onManual = () => reload();
    window.addEventListener(DATA_REFRESH_EVENT, onManual);
    return () => {
      clearInterval(timer);
      window.removeEventListener(DATA_REFRESH_EVENT, onManual);
      seq.current += 1;
    };
  }, [reload]);

  useEffect(() => {
    const minute = window.setInterval(() => setTick((value) => value + 1), 60_000);
    return () => window.clearInterval(minute);
  }, []);

  const now = panelNow().getTime() + tick;
  const visible = sortQuotaProviders((payload?.providers ?? []).filter(isVisibleQuotaProvider));

  return (
    <Panel
      icon={<Gauge size={15} />}
      kicker="MODEL QUOTA"
      title={t("模型额度")}
      className="rt-quota-panel"
    >
      <p className="rt-quota-hint">{t("显示已用比例；API 账号显示余额。")}</p>
      {loading && !loaded && <DataState loading />}
      {error && (
        <DataState error={error} stale={loaded} onRetry={reload} />
      )}
      {loaded && !error && visible.length === 0 && <DataState empty={t(EMPTY_HINT)} />}
      {loaded && !error && visible.length > 0 && (
        <div className="rt-quota-list">
          {visible.map((entry) => (
            <ProviderCard
              key={`${entry.provider}-${entry.account_fp ?? entry.nodes.join(",")}`}
              entry={entry}
              now={now}
              ordinal={accountOrdinal(visible, entry)}
            />
          ))}
        </div>
      )}
    </Panel>
  );
}
