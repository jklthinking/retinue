import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import DispatchMap from "../components/DispatchMap";
import {
  DEFAULT_THEME,
  ThemeProvider,
  ThemeSwitcher,
  courtVocab,
  neutralVocab,
  useVocab,
} from "../theme";
import { AGENT_ONE, AGENT_TWO } from "./helpers";

afterEach(cleanup);
beforeEach(() => {
  window.localStorage.clear();
});

function VocabProbe() {
  const vocab = useVocab();
  return (
    <div>
      <span data-testid="app-title">{vocab.appTitle}</span>
      <span data-testid="central-hub">{vocab.centralHub}</span>
      <span data-testid="node-throne">{vocab.nodeThrone}</span>
    </div>
  );
}

describe("主题词表", () => {
  it("默认导出中性词表，不含宫廷词汇", () => {
    expect(DEFAULT_THEME).toBe("neutral");
    for (const value of Object.values(neutralVocab)) {
      expect(value).not.toMatch(/组织|王座|众卿/);
    }
    // 宫廷预设保留原有风味文案
    expect(courtVocab.appTitle).toBe("众卿任务台");
    expect(courtVocab.centralHub).toBe("组织中枢");
    expect(courtVocab.nodeThrone).toBe("王座 Throne");
  });

  it("无 Provider 时组件回退到中性词表", () => {
    render(<VocabProbe />);
    expect(screen.getByTestId("app-title")).toHaveTextContent("任务台");
    expect(screen.getByTestId("central-hub")).toHaveTextContent("任务中枢");
  });

  it("ThemeProvider 按预设切换文案", () => {
    render(
      <ThemeProvider initialTheme="court">
        <VocabProbe />
      </ThemeProvider>
    );
    expect(screen.getByTestId("app-title")).toHaveTextContent("众卿任务台");
    expect(screen.getByTestId("node-throne")).toHaveTextContent("王座 Throne");
  });

  it("ThemeSwitcher 切换主题并持久化选择", async () => {
    const user = userEvent.setup();
    render(
      <ThemeProvider>
        <ThemeSwitcher />
        <VocabProbe />
      </ThemeProvider>
    );
    expect(screen.getByTestId("app-title")).toHaveTextContent("任务台");

    await user.selectOptions(screen.getByLabelText("界面主题词"), "court");
    expect(screen.getByTestId("app-title")).toHaveTextContent("众卿任务台");
    expect(window.localStorage.getItem("retinue.theme")).toBe("court");

    await user.selectOptions(screen.getByLabelText("界面主题词"), "neutral");
    expect(screen.getByTestId("app-title")).toHaveTextContent("任务台");
    expect(window.localStorage.getItem("retinue.theme")).toBe("neutral");
  });

  it("ThemeProvider 从 localStorage 读取已存主题", () => {
    window.localStorage.setItem("retinue.theme", "court");
    render(
      <ThemeProvider>
        <VocabProbe />
      </ThemeProvider>
    );
    expect(screen.getByTestId("central-hub")).toHaveTextContent("组织中枢");
  });

  it("组件文案随主题切换（DispatchMap）", async () => {
    const user = userEvent.setup();
    render(
      <ThemeProvider>
        <ThemeSwitcher />
        <DispatchMap tasks={[]} actors={[AGENT_ONE, AGENT_TWO]} />
      </ThemeProvider>
    );
    expect(screen.getByText(neutralVocab.membersAgents)).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("界面主题词"), "court");
    expect(screen.getByText(courtVocab.membersAgents)).toBeInTheDocument();
    expect(screen.queryByText(neutralVocab.membersAgents)).not.toBeInTheDocument();
  });
});
