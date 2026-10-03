export const demoMode = import.meta.env.VITE_DEMO_MODE === "1";

/** Frozen calendar day baked into the public demo API snapshots. */
export function demoToday(): string | undefined {
  const value = import.meta.env.VITE_DEMO_TODAY;
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

/** Match the fixed offline snapshot clock without affecting live views. */
export function panelNow(): Date {
  if (demoMode) {
    const value = import.meta.env.VITE_DEMO_NOW;
    if (typeof value === "string" && value.length > 0) {
      const configured = new Date(value);
      if (Number.isFinite(configured.getTime())) return configured;
    }
  }
  const day = demoMode ? demoToday() : undefined;
  return day ? new Date(`${day}T09:42:00Z`) : new Date();
}
