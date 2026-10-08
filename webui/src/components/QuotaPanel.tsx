import { useCallback, useEffect, useRef, useState } from "react";
import { Gauge, RefreshCw } from "lucide-react";
import { t, useI18n } from "../i18n";
import { ApiError, readErrorMessage } from "../api";
import { panelNow } from "../demo";
import {
  fetchQuota,
  fetchQuotaRefresh,
  startQuotaRefresh,
  quotaRefreshMessage,
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
  const refreshSeq = useRef(0);
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const refreshingRef = useRef(false);
  const requestKey = useRef<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshMessage, setRefreshMessage] = useState<string | null>(null);

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

  const refresh = useCallback(async () => {
    if (refreshingRef.current || !payload?.can_refresh) return;
    refreshingRef.current = true;
    setRefreshing(true);
    setRefreshMessage("正在提交查询…");
    const ticket = ++refreshSeq.current;
    // Reuse the key after an uncertain network failure so a retry cannot
    // create another batch. Keys are discarded only on a definitive response.
    requestKey.current ??= Array.from(crypto.getRandomValues(new Uint8Array(16)),
      (value) => value.toString(16).padStart(2, "0")).join("");
    const until = Date.now() + 6 * 60_000;
    const finish = () => {
      if (ticket !== refreshSeq.current) return;
      refreshingRef.current = false;
      setRefreshing(false);
    };
    try {
      const batch = await startQuotaRefresh(requestKey.current);
      if (ticket !== refreshSeq.current) return;
      requestKey.current = null;
      const watch = async (value: typeof batch) => {
        if (ticket !== refreshSeq.current) return;
        setRefreshMessage(quotaRefreshMessage(value));
        if (!value.requests.some((row) => row.status === "queued" || row.status === "claimed")) {
          reload();
          finish();
          return;
        }
        if (Date.now() >= until) {
          setRefreshMessage("查询超时，仍为旧数据。");
          finish();
          return;
        }
        refreshTimer.current = setTimeout(() => {
          void fetchQuotaRefresh(batch.batch_id).then(watch).catch(() => {
            if (ticket !== refreshSeq.current) return;
            setRefreshMessage("查询状态无法确认，仍显示上次数据。请重试。");
            finish();
          });
        }, Math.max(1000, Math.min(5000, value.poll_after_ms || 3000)));
      };
      await watch(batch);
    } catch (reason) {
      if (ticket !== refreshSeq.current) return;
      if (reason instanceof ApiError && reason.status >= 400 && reason.status < 500) requestKey.current = null;
      setRefreshMessage(reason instanceof ApiError && reason.status === 429
        ? "查询过于频繁，请稍后再试。" : "查询状态无法确认，仍显示上次数据。请重试。");
      finish();
    }
  }, [payload?.can_refresh, reload]);

  useEffect(() => {
    reload();
    const timer = setInterval(reload, BOARD_REFRESH_MS);
    const onManual = () => reload();
    window.addEventListener(DATA_REFRESH_EVENT, onManual);
    return () => {
      clearInterval(timer);
      window.removeEventListener(DATA_REFRESH_EVENT, onManual);
      seq.current += 1;
      refreshSeq.current += 1;
      if (refreshTimer.current) clearTimeout(refreshTimer.current);
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
      tools={payload?.refresh_enabled && (
        <button type="button" className="rt-quota-refresh" onClick={() => void refresh()}
          disabled={!payload.can_refresh || refreshing}
          title={!payload.can_refresh ? t("需要管理员权限才能查询额度。") : undefined}
          aria-busy={refreshing}>
          <RefreshCw size={14} />{t(refreshing ? "查询中…" : "刷新查询")}
        </button>
      )}
    >
      <p className="rt-quota-hint">{t("显示已用比例；API 账号显示余额。")}</p>
      {refreshMessage && <p className="rt-quota-refresh-status" role="status" aria-live="polite">{t(refreshMessage)}</p>}
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
