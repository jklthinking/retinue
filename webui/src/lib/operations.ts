import { t, getLanguage } from "../i18n";
import { useEffect, useRef, useState } from "react";
import { readErrorMessage } from "../api";
import { startVisiblePolling } from "./refresh";

export type OperationsDays = 1 | 7 | 30;
export const rangeLabel = (days: number) => days === 1 ? t("今天") : t("近 {v0} 日", { v0: days });

/** Translate only server-generated catalog/discovery messages. These bounded
 * patterns must never be applied to task text, user notes, or transcripts. */
export function operationsSystemText(source: string): string {
  const patterns: Array<[RegExp, string]> = [
    [/^为 (\d+) 张任务卡补充可验证的 acceptance。$/, "为 {count} 张任务卡补充可验证的 acceptance。"],
    [/^为 (\d+) 个执行型智能体补齐 runtime。$/, "为 {count} 个执行型智能体补齐 runtime。"],
    [/^为 (\d+) 名人类角色登记接入点或所属节点。$/, "为 {count} 名人类角色登记接入点或所属节点。"],
    [/^运行数据检查发现 (\d+) 项待核对记录；在质量检查中查看来源时间、失效租约与身份登记。历史记录继续保留。$/, "运行数据检查发现 {count} 项待核对记录；在质量检查中查看来源时间、失效租约与身份登记。历史记录继续保留。"],
    [/^有 (\d+) 个运行时尚未关联智能体；确认后即可登记。$/, "有 {count} 个运行时尚未关联智能体；确认后即可登记。"],
    [/^有 (\d+) 位智能体需要补齐设备、模型、节点能力或会话同步。$/, "有 {count} 位智能体需要补齐设备、模型、节点能力或会话同步。"],
  ];
  for (const [pattern, template] of patterns) {
    const match = pattern.exec(source);
    if (match) return t(template, { count: match[1] });
  }
  return t(source);
}

export function measuredNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

export function formatTokens(value: number | null | undefined): string {
  const count = measuredNumber(value);
  if (count === null) return t("未知");
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(1)}M`;
  if (count >= 1_000) return `${(count / 1_000).toFixed(1)}k`;
  return count.toLocaleString(getLanguage());
}
export function exactTokens(value: number | null | undefined): string {
  const count = measuredNumber(value);
  return count === null ? t("模型 token 数未记录") : String(count) + t(" 个模型 token（原始整数）");
}

export function sourceTime(value?: string | number | null): string {
  if (value === null || value === undefined || value === "") return t("未记录");
  const timestamp = typeof value === "number" && value < 10_000_000_000 ? value * 1000 : value;
  const date = new Date(timestamp);
  return Number.isFinite(date.getTime()) ? date.toLocaleString(getLanguage(), { hour12: false }) : t("未记录");
}

/** A page read is not a collector heartbeat. Keep the last successful payload
 * on failure, and retain its range key until the next successful read. */
export function useOperationsRead<T>(key: string, reader: () => Promise<T>) {
  const [data, setData] = useState<T | null>(null);
  const [readKey, setReadKey] = useState("");
  const [fetchedAt, setFetchedAt] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const generation = useRef(0);
  useEffect(() => {
    const current = ++generation.current;
    const stop = startVisiblePolling(async () => {
      setRefreshing(true);
      try {
        const value = await reader();
        if (generation.current !== current) return;
        setData(value);
        setReadKey(key);
        setFetchedAt(new Date().toISOString());
        setError(null);
      } catch (reason) {
        if (generation.current === current) setError(readErrorMessage(reason));
      } finally {
        if (generation.current === current) setRefreshing(false);
      }
    }, 60_000);
    return () => { generation.current++; stop(); };
  }, [key, reader]);
  return { data, readKey, fetchedAt, refreshing, error };
}
