import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  DEFAULT_THEME,
  THEME_PRESETS,
  isThemeId,
  type ThemeId,
  type ThemeVocab,
} from "./vocab";

const STORAGE_KEY = "retinue.theme";

function readStoredTheme(): ThemeId {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (isThemeId(stored)) return stored;
  } catch {
    // localStorage may be unavailable (private mode, test doubles).
  }
  return DEFAULT_THEME;
}

export interface ThemeContextValue {
  themeId: ThemeId;
  vocab: ThemeVocab;
  setTheme: (id: ThemeId) => void;
}

const ThemeContext = createContext<ThemeContextValue>({
  themeId: DEFAULT_THEME,
  vocab: THEME_PRESETS[DEFAULT_THEME].vocab,
  setTheme: () => undefined,
});

export function ThemeProvider({
  initialTheme,
  children,
}: {
  /** Test hook: pin a theme instead of reading localStorage. */
  initialTheme?: ThemeId;
  children: ReactNode;
}) {
  const [themeId, setThemeId] = useState<ThemeId>(
    () => initialTheme ?? readStoredTheme()
  );
  const setTheme = useCallback((id: ThemeId) => {
    setThemeId(id);
    try {
      window.localStorage.setItem(STORAGE_KEY, id);
    } catch {
      // Persisting the preference is best-effort.
    }
  }, []);
  const value = useMemo<ThemeContextValue>(
    () => ({ themeId, vocab: THEME_PRESETS[themeId].vocab, setTheme }),
    [themeId, setTheme]
  );
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  return useContext(ThemeContext);
}

/** The active theme vocabulary; neutral when no provider is mounted. */
export function useVocab(): ThemeVocab {
  return useContext(ThemeContext).vocab;
}

export function ThemeSwitcher() {
  const { themeId, setTheme } = useTheme();
  return (
    <label className="theme-switcher">
      <span>主题词</span>
      <select
        aria-label="界面主题词"
        value={themeId}
        onChange={(event) => setTheme(event.target.value as ThemeId)}
      >
        {Object.entries(THEME_PRESETS).map(([id, preset]) => (
          <option key={id} value={id}>
            {preset.label}
          </option>
        ))}
      </select>
    </label>
  );
}
