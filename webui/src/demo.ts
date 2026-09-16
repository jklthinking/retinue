export const demoMode = import.meta.env.VITE_DEMO_MODE === "1";

/** Frozen calendar day baked into the public demo API snapshots. */
export function demoToday(): string | undefined {
  const value = import.meta.env.VITE_DEMO_TODAY;
  return typeof value === "string" && value.length > 0 ? value : undefined;
}
