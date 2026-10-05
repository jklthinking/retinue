import { t } from "../i18n";
import type { ActorInfo } from "../types";

export function isSessionSync(actor: ActorInfo): boolean {
  return actor.kind === "agent" && (
    actor.model.trim().toLowerCase().startsWith("session-index-v")
    || actor.id.trim().toLowerCase().endsWith("-session-sync")
  );
}

export function registeredModel(model: string): { label: string; state: "known" | "alias" | "unknown" } {
  const value = model.trim();
  const normalized = value.toLowerCase();
  if (!value || ["configured-at-runtime", "unknown", "待确认", "未登记", "n/a"].includes(normalized)) {
    return { label: t("型号待确认"), state: "unknown" };
  }
  if (["opus", "sonnet", "haiku", "auto", "default"].includes(normalized)) {
    return { label: value, state: "alias" };
  }
  return { label: value, state: "known" };
}

export function rosterIdentity(actor: ActorInfo): string {
  if (actor.kind !== "agent" || isSessionSync(actor)) return actor.display_name || actor.id;
  return (actor.node || t("设备待绑定")) + " · " + registeredModel(actor.model).label;
}
