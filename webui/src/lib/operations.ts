import { useEffect, useRef, useState } from "react";
import { readErrorMessage } from "../api";
import { startVisiblePolling } from "./refresh";

export type OperationsDays = 1 | 7 | 30;
export const rangeLabel = (days: number) => days === 1 ? "今天" : `近 ${days} 日`;

export function measuredNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

export function formatTokens(value: number | null | undefined): string {
  const count = measuredNumber(value);
  if (count === null) return "未知";
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(1)}M`;
  if (count >= 1_000) return `${(count / 1_000).toFixed(1)}k`;
  return count.toLocaleString("zh-CN");
}
export function exactTokens(value: number | null | undefined): string {
  const count = measuredNumber(value);
  return count === null ? "模型 token 数未记录" : String(count) + " 个模型 token（原始整数）";
}

export function sourceTime(value?: string | number | null): string {
  if (value === null || value === undefined || value === "") return "未记录";
  const timestamp = typeof value === "number" && value < 10_000_000_000 ? value * 1000 : value;
  const date = new Date(timestamp);
  return Number.isFinite(date.getTime()) ? date.toLocaleString("zh-CN", { hour12: false }) : "未记录";
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
