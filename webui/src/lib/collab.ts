import { t } from "../i18n";
import type { Task } from "../types";
import { panelNow } from "../demo";

export const STAGES = ["发单", "接单", "执行", "交付审校", "完成"] as const;

/** Pipeline stage index for the dispatch → done lifecycle. */
export function stageIndex(task: Task): number {
  if (task.status === "done") return 4;
  if (task.status === "handoff") return 3;
  if (task.status === "doing" || task.status === "blocked") return 2;
  return task.open_dispatch ? 0 : 1;
}

export function postedAt(task: Task): string | null {
  return task.chain.length ? task.chain[0].at : null;
}

export function claimedAt(task: Task): string | null {
  // a claim keeps the card queued while the holder changes — that exact
  // signature distinguishes it from handoffs/reassigns in later states
  for (const event of task.chain) {
    if (
      event.from_status === "queued" &&
      event.to_status === "queued" &&
      event.from_holder &&
      event.to_holder &&
      event.from_holder !== event.to_holder
    ) {
      return event.at;
    }
  }
  // assigned at creation counts as claimed immediately
  return task.open_dispatch ? null : postedAt(task);
}

export function localDateKey(date = panelNow()): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(
    date.getDate()
  ).padStart(2, "0")}`;
}

export function doneAt(task: Task): string | null {
  for (const event of task.chain) {
    if (event.to_status === "done") return event.at;
  }
  return null;
}

export function lastEvent(task: Task) {
  return task.chain.length ? task.chain[task.chain.length - 1] : null;
}

export function elapsedText(fromIso: string | null, toIso?: string | null): string {
  if (!fromIso) return "—";
  const from = new Date(fromIso).getTime();
  const to = toIso ? new Date(toIso).getTime() : panelNow().getTime();
  const minutes = Math.max(0, Math.round((to - from) / 60_000));
  if (minutes < 60) return t("{v0} 分钟", { v0: minutes });
  const hours = minutes / 60;
  if (hours < 48) return t("{v0} 小时", { v0: hours.toFixed(1) });
  return t("{v0} 天", { v0: (hours / 24).toFixed(1) });
}
