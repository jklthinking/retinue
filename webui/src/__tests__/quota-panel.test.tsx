import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import QuotaPanel from "../components/QuotaPanel";
import { ThemeProvider } from "../theme";
import userEvent from "@testing-library/user-event";
import { LanguageSwitcher, LocaleProvider } from "../i18n";
import {
  formatFetchedAgo,
  formatResetLine,
  formatShanghaiDateTime,
  formatUsedPercent,
  sortQuotaProviders,
  type QuotaProviderEntry,
  type QuotaResponse,
} from "../lib/quota";
import { mockFetch } from "./helpers";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  window.history.replaceState(null, "", "/");
  window.localStorage.clear();
});

function makeProvider(overrides: Partial<QuotaProviderEntry> = {}): QuotaProviderEntry {
  return {
    provider: "claude",
    kind: "subscription",
    status: "ok",
    plan: "pro",
    account_fp: "abcdef1234567890",
    nodes: ["sample-node"],
    windows: [
      {
        key: "five_hour",
        label: "five_hour",
        period: "5h",
        used_percent: 42,
        used: null,
        limit: null,
        unit: "percent",
        resets_at: "2026-10-07T09:21:00+00:00",
        raw_reset: null,
      },
    ],
    balance: null,
    fetched_at: "2026-10-04T02:00:00+00:00",
    error: null,
    stale: false,
    ...overrides,
  };
}

function makeQuota(providers: QuotaProviderEntry[]): QuotaResponse {
  return { generated_at: "2026-10-04T03:00:00+00:00", providers };
}

function renderPanel() {
  render(
    <LocaleProvider>
      <ThemeProvider>
        <LanguageSwitcher />
        <QuotaPanel />
      </ThemeProvider>
    </LocaleProvider>
  );
}

describe("quota helpers", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-04T01:00:00+08:00"));
    window.history.replaceState(null, "", "/?lang=zh-CN");
    render(<LocaleProvider><span /></LocaleProvider>);
  });

  it("formats reset time in Beijing with relative countdown", () => {
    expect(formatShanghaiDateTime("2026-10-07T09:21:00+00:00")).toBe("10月7日 17:21");
    expect(formatResetLine("2026-10-07T09:21:00+00:00")).toBe(
      "重置：10月7日 17:21（3天16小时后）"
    );
  });

  it("sorts providers by tightest window", () => {
    const sorted = sortQuotaProviders([
      makeProvider({ provider: "claude", windows: [{ ...makeProvider().windows[0], used_percent: 20 }] }),
      makeProvider({
        provider: "codex",
        windows: [{ ...makeProvider().windows[0], key: "weekly", period: "weekly", used_percent: 88 }],
      }),
    ]);
    expect(sorted.map((row) => row.provider)).toEqual(["codex", "claude"]);
  });

  it("reports fetched age in Chinese", () => {
    const now = Date.parse("2026-10-04T01:00:00+00:00");
    expect(formatFetchedAgo("2026-10-04T00:00:00+00:00", now)).toBe("查询于 1 小时前");
  });

  it("formats used percent with integer or one decimal", () => {
    expect(formatUsedPercent(null)).toBe("—");
    expect(formatUsedPercent(1.0306666666666666)).toBe("1.0%");
    expect(formatUsedPercent(4.180967741935484)).toBe("4.2%");
    expect(formatUsedPercent(98.69)).toBe("99%");
    expect(formatUsedPercent(42)).toBe("42%");
  });
});

describe("QuotaPanel 模型额度", () => {
  beforeEach(() => {
    vi.useRealTimers();
    vi.setSystemTime(new Date("2026-10-04T01:00:00+08:00"));
    window.history.replaceState(null, "", "/?lang=zh-CN");
  });

  it("renders multiple providers with labels and window rows", async () => {
    mockFetch({
      "api/quota": () => ({
        body: makeQuota([
          makeProvider(),
          makeProvider({
            provider: "codex",
            plan: "plus",
            account_fp: "112233445566",
            windows: [
              {
                key: "monthly.plan",
                label: "monthly.plan",
                period: "monthly",
                used_percent: 71,
                used: null,
                limit: null,
                unit: "percent",
                resets_at: "2026-11-01T00:00:00+00:00",
                raw_reset: null,
              },
            ],
          }),
        ]),
      }),
    });
    renderPanel();

    expect(await screen.findByRole("heading", { name: "模型额度" })).toBeInTheDocument();
    expect(screen.getByText("Claude")).toBeInTheDocument();
    expect(screen.getByText("Codex (ChatGPT)")).toBeInTheDocument();
    // Account fingerprints are never displayed.
    expect(screen.queryByText(/abcdef|112233/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^账号/)).not.toBeInTheDocument();
    expect(screen.getByText("5 小时")).toBeInTheDocument();
    expect(screen.getByText("本月套餐")).toBeInTheDocument();
    expect(screen.getByText(/重置：10月7日 17:21/)).toBeInTheDocument();
  });

  it("shows error with last_ok snapshot and stale badge", async () => {
    mockFetch({
      "api/quota": () => ({
        body: makeQuota([
          makeProvider({
            status: "error",
            error: "Quota query failed",
            stale: true,
            windows: [],
            last_ok: {
              fetched_at: "2026-10-03T12:00:00+00:00",
              windows: [
                {
                  key: "five_hour",
                  label: "five_hour",
                  period: "5h",
                  used_percent: 55,
                  used: null,
                  limit: null,
                  unit: "percent",
                  resets_at: "2026-10-04T05:00:00+00:00",
                  raw_reset: null,
                },
              ],
            },
          }),
        ]),
      }),
    });
    renderPanel();

    expect(await screen.findByText("Quota query failed")).toBeInTheDocument();
    expect(screen.getByText("数据过期")).toBeInTheDocument();
    const lastOk = screen.getByLabelText("上次成功数据");
    expect(within(lastOk).getByText("5 小时")).toBeInTheDocument();
    expect(screen.getByText(/上次数据（10月3日 20:00）/)).toBeInTheDocument();
  });

  it("hides consent_missing providers and shows empty state", async () => {
    mockFetch({
      "api/quota": () => ({
        body: makeQuota([
          makeProvider({ status: "consent_missing" }),
          makeProvider({ status: "not_configured" }),
        ]),
      }),
    });
    renderPanel();

    expect(await screen.findByText(/还没有额度数据/)).toBeInTheDocument();
    expect(screen.queryByText("Claude")).not.toBeInTheDocument();
  });

  it("orders cards with the tightest usage first", async () => {
    mockFetch({
      "api/quota": () => ({
        body: makeQuota([
          makeProvider({ provider: "claude", windows: [{ ...makeProvider().windows[0], used_percent: 10 }] }),
          makeProvider({
            provider: "grok",
            windows: [{ ...makeProvider().windows[0], key: "productUsage", period: "weekly", used_percent: 95 }],
          }),
        ]),
      }),
    });
    renderPanel();
    await screen.findByText("Grok");

    const titles = screen.getAllByRole("article").map((node) => node.getAttribute("aria-label"));
    expect(titles[0]).toContain("Grok");
    expect(titles[1]).toContain("Claude");
  });

  it("switches the loaded quota cards between English and Chinese", async () => {
    window.history.replaceState(null, "", "/?lang=en");
    mockFetch({ "api/quota": () => ({ body: makeQuota([makeProvider()]) }) });
    renderPanel();
    expect(await screen.findByRole("heading", { name: "Model quota" })).toBeInTheDocument();
    expect(screen.getByText("5 hours")).toBeInTheDocument();
    expect(screen.getByText(/Reset \(UTC\+08:00\):/)).toBeInTheDocument();
    expect(screen.getByRole("article", { name: "Claude quota" })).toHaveTextContent("42%");

    await userEvent.setup().selectOptions(screen.getByLabelText("Language / 语言"), "zh-CN");
    expect(screen.getByRole("heading", { name: "模型额度" })).toBeInTheDocument();
    expect(screen.getByText("5 小时")).toBeInTheDocument();
    expect(screen.getByRole("article", { name: "Claude 额度" })).toHaveTextContent("42%");
  });

  it("distinguishes multiple accounts without displaying their fingerprints", async () => {
    window.history.replaceState(null, "", "/?lang=en");
    mockFetch({ "api/quota": () => ({ body: makeQuota([
      makeProvider(), makeProvider({ account_fp: "0011223344556677" }),
    ]) }) });
    renderPanel();
    expect(await screen.findByText("Account 1")).toBeInTheDocument();
    expect(screen.getByText("Account 2")).toBeInTheDocument();
    expect(screen.queryByText(/abcdef1234567890|0011223344556677/)).not.toBeInTheDocument();
  });

  it("does not invent refreshed usage when a reset time has passed", async () => {
    window.history.replaceState(null, "", "/?lang=en");
    mockFetch({ "api/quota": () => ({ body: makeQuota([makeProvider({
      windows: [{ ...makeProvider().windows[0], resets_at: "2025-01-01T00:00:00Z" }],
    })]) }) });
    renderPanel();
    expect(await screen.findByText(/reset time reached; awaiting the next report/)).toBeInTheDocument();
    expect(screen.getByRole("article", { name: "Claude quota" })).toHaveTextContent("42%");
  });
});
