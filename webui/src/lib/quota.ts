import { api } from "../api";
import { getLanguage, t } from "../i18n";

export type QuotaProviderId =
  | "claude"
  | "codex"
  | "grok"
  | "cursor"
  | "kimi"
  | "moonshot"
  | (string & {});

export type QuotaStatus =
  | "ok"
  | "error"
  | "expired"
  | "not_configured"
  | "consent_missing";

export interface QuotaWindow {
  key: string;
  label: string;
  period: string;
  used_percent: number | null;
  used: number | null;
  limit: number | null;
  unit: string;
  resets_at: string | null;
  raw_reset?: string | number | null;
}

export interface QuotaBalance {
  amount: number;
  currency: string;
}

export interface QuotaLastOk {
  windows: QuotaWindow[];
  fetched_at: string;
}

export interface QuotaProviderEntry {
  provider: QuotaProviderId;
  kind: "subscription" | "api";
  status: QuotaStatus;
  plan: string | null;
  account_fp: string | null;
  nodes: string[];
  windows: QuotaWindow[];
  balance: QuotaBalance | null;
  fetched_at: string;
  error: string | null;
  stale: boolean;
  last_ok?: QuotaLastOk | null;
}

export interface QuotaResponse {
  generated_at: string;
  providers: QuotaProviderEntry[];
}

const SHANGHAI = "Asia/Shanghai";

const PROVIDER_LABELS: Record<string, string> = {
  claude: "Claude",
  codex: "Codex (ChatGPT)",
  grok: "Grok",
  cursor: "Cursor",
  kimi: "Kimi Code",
  moonshot: "Moonshot API",
};

const WINDOW_KEY_LABELS: Record<string, string> = {
  "monthly.plan": "本月套餐",
  "monthly.auto": "本月 Auto",
  "monthly.api": "本月 API",
  "monthly.on_demand": "按量",
  "weekly.grok_bot": "Grok Bot 每周",
  productUsage: "GrokBuild 每周",
};

const PERIOD_LABELS: Record<string, string> = {
  "5h": "5 小时",
  weekly: "每周",
  monthly: "本月",
  daily: "每日",
};

export function providerLabel(provider: string): string {
  return PROVIDER_LABELS[provider] ?? provider;
}

export function windowLabel(window: QuotaWindow): string {
  if (WINDOW_KEY_LABELS[window.key]) return t(WINDOW_KEY_LABELS[window.key]);
  if (PERIOD_LABELS[window.period]) return t(PERIOD_LABELS[window.period]);
  return window.label || window.key;
}

export function isVisibleQuotaProvider(entry: QuotaProviderEntry): boolean {
  return entry.status !== "consent_missing" && entry.status !== "not_configured";
}

export function maxUsedPercent(windows: QuotaWindow[]): number {
  const values = windows.map((row) => row.used_percent).filter((v): v is number => v != null);
  return values.length ? Math.max(...values) : -1;
}

export function sortQuotaProviders(entries: QuotaProviderEntry[]): QuotaProviderEntry[] {
  return [...entries].sort((left, right) => {
    const gap = maxUsedPercent(right.windows) - maxUsedPercent(left.windows);
    if (gap !== 0) return gap;
    return providerLabel(left.provider).localeCompare(providerLabel(right.provider), getLanguage());
  });
}

export function progressTone(percent: number | null): "is-primary" | "is-amber" | "is-red" | "" {
  if (percent == null) return "";
  if (percent >= 90) return "is-red";
  if (percent >= 70) return "is-amber";
  return "is-primary";
}

/** ≥10% shown as integer; below 10% one decimal place (e.g. 1.0%, 4.2%). */
export function formatUsedPercent(percent: number | null): string {
  if (percent == null) return "—";
  if (percent >= 10) return `${Math.round(percent)}%`;
  const rounded = Math.round(percent * 10) / 10;
  return `${rounded.toFixed(1)}%`;
}

function shanghaiParts(date: Date, options: Intl.DateTimeFormatOptions): Intl.DateTimeFormatPart[] {
  return new Intl.DateTimeFormat("zh-CN", { timeZone: SHANGHAI, ...options }).formatToParts(date);
}

function partValue(parts: Intl.DateTimeFormatPart[], type: Intl.DateTimeFormatPartTypes): string {
  return parts.find((item) => item.type === type)?.value ?? "";
}

/** Calendar date and clock in Beijing, e.g. 10月7日 17:21 */
export function formatShanghaiDateTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  if (getLanguage() === "en") {
    return new Intl.DateTimeFormat("en", {
      timeZone: SHANGHAI, month: "short", day: "numeric",
      hour: "2-digit", minute: "2-digit", hour12: false,
    }).format(date);
  }
  const parts = shanghaiParts(date, {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  return `${partValue(parts, "month")}月${partValue(parts, "day")}日 ${partValue(parts, "hour")}:${partValue(parts, "minute")}`;
}

function relativeFuturePhrase(targetMs: number, nowMs: number): string {
  const diff = Math.max(0, targetMs - nowMs);
  const minute = 60_000;
  if (diff < minute) return t("不到 1 分钟后");
  if (diff < 60 * minute) return t("{number} 分钟后", { number: Math.floor(diff / minute) });
  const hours = Math.floor(diff / (60 * minute));
  const days = Math.floor(diff / (24 * 60 * minute));
  if (diff < 24 * 60 * minute) return t("{number} 小时后", { number: hours });
  const remHours = hours % 24;
  if (remHours === 0) return t("{number} 天后", { number: days });
  return t("{days}天{hours}小时后", { days, hours: remHours });
}

export function formatResetLine(resetsAt: string | null, now = Date.now()): string {
  if (!resetsAt) return t("重置：—");
  const target = new Date(resetsAt).getTime();
  if (Number.isNaN(target)) return t("重置：—");
  const stamp = formatShanghaiDateTime(resetsAt);
  if (target <= now) return t("重置：{stamp}（重置时间已到，待下次查询更新）", { stamp });
  return t("重置：{stamp}（{relative}）", { stamp, relative: relativeFuturePhrase(target, now) });
}

export function formatFetchedAgo(iso: string, now = Date.now()): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return t("查询时间未知");
  const diff = Math.max(0, now - date.getTime());
  const minute = 60_000;
  if (diff < minute) return t("查询于刚刚");
  if (diff < 60 * minute) return t("查询于 {number} 分钟前", { number: Math.floor(diff / minute) });
  if (diff < 24 * 60 * minute) return t("查询于 {number} 小时前", { number: Math.floor(diff / (60 * minute)) });
  const days = Math.floor(diff / (24 * 60 * minute));
  return t("查询于 {number} 天前", { number: days });
}

export function formatBalance(balance: QuotaBalance | null): string | null {
  if (!balance) return null;
  const amount = balance.amount.toLocaleString(getLanguage(), { maximumFractionDigits: 2 });
  return t("余额 {amount} {currency}", { amount, currency: balance.currency });
}

export async function fetchQuota(): Promise<QuotaResponse> {
  return api.get<QuotaResponse>("/api/quota");
}
