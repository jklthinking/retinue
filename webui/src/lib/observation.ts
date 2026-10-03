export const TELEMETRY_MAX_AGE_MS = 30 * 60 * 1000;
export type ObservationState = "fresh" | "stale" | "unknown" | "clock_skew";

export function observationState(value?: string | null, now = Date.now(), maxAge = TELEMETRY_MAX_AGE_MS): ObservationState {
  if (!value) return "unknown";
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return "unknown";
  if (timestamp - now > 5 * 60 * 1000) return "clock_skew";
  return now - timestamp > maxAge ? "stale" : "fresh";
}

/** Match the explicit calendar used by Ops, rather than the viewer device's
 * local date. Legacy usage buckets are still not re-bucketed. */
export function operationsCalendarDay(now = Date.now()): string {
  const parts = new Intl.DateTimeFormat("en", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(new Date(now));
  const value = (kind: Intl.DateTimeFormatPartTypes) => parts.find((part) => part.type === kind)?.value ?? "";
  return value("year") + "-" + value("month") + "-" + value("day");
}

interface GatewayObservation { healthy: boolean; current_process_healthy?: boolean | null; observed_at?: string | null; snapshot_stale?: boolean }
interface SourceObservation { stale?: boolean; snapshot_at?: string | null; generated_at?: string | null }

/** A process observation is not a worker availability assertion. Explicit
 * null from the server must stay unknown even if the historical flag is true. */
export function gatewayProcessState(gateway: GatewayObservation, node?: SourceObservation, actor?: { observed_at?: string | null; snapshot_stale?: boolean }): boolean | null {
  if (gateway.snapshot_stale || actor?.snapshot_stale || node?.stale) return null;
  const observedAt = gateway.observed_at ?? actor?.observed_at ?? node?.snapshot_at ?? node?.generated_at;
  const state = observationState(observedAt);
  if (state !== "fresh") return null;
  if (gateway.current_process_healthy !== undefined) return gateway.current_process_healthy;
  return typeof gateway.healthy === "boolean" ? gateway.healthy : null;
}
