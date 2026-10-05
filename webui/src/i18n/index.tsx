import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import shared from "./dictionaries/shared";
import workspace from "./dictionaries/workspace";
import operations from "./dictionaries/operations";
import remaining from "./dictionaries/remaining";

export type Language = "zh-CN" | "en";
export type TranslationValues = Record<string, string | number | null | undefined>;
const STORAGE_KEY = "retinue.language";
// Common menu, theme and stage labels have one consistent wording across pages.
const translations: Record<string, string> = { ...workspace, ...operations, ...remaining, ...shared };
let activeLanguage: Language = "zh-CN";

function validLanguage(value: unknown): value is Language {
  return value === "en" || value === "zh-CN";
}

/** Explicit links take precedence over this browser's saved preference. */
export function readLanguage(): Language {
  if (typeof window !== "undefined") {
    const requested = new URLSearchParams(window.location.search).get("lang");
    if (validLanguage(requested)) return requested;
    try {
      const saved = window.localStorage.getItem(STORAGE_KEY);
      if (validLanguage(saved)) return saved;
    } catch {
      // Browsers may block preference storage; links still work.
    }
  }
  const buildDefault = import.meta.env.VITE_DEFAULT_LANGUAGE;
  return validLanguage(buildDefault) ? buildDefault : "zh-CN";
}

function translate(language: Language, source: string, values?: TranslationValues): string {
  const text = language === "en" && Object.prototype.hasOwnProperty.call(translations, source)
    ? translations[source] : source;
  return text.replace(/\{([a-zA-Z][a-zA-Z0-9_]*)\}/g, (placeholder, key: string) =>
    values && Object.prototype.hasOwnProperty.call(values, key) ? String(values[key] ?? "") : placeholder
  );
}

/** Use only for interface labels. Task text and other operator data are verbatim. */
export function t(source: string, values?: TranslationValues): string {
  return translate(activeLanguage, source, values);
}

export function getLanguage(): Language {
  return activeLanguage;
}

interface I18nContextValue {
  language: Language;
  setLanguage: (language: Language) => void;
  t: typeof t;
}

const I18nContext = createContext<I18nContextValue>({
  language: "zh-CN",
  setLanguage: () => undefined,
  t: (source, values) => translate("zh-CN", source, values),
});

export function LocaleProvider({ children }: { children: ReactNode }) {
  const [language, updateLanguage] = useState<Language>(readLanguage);
  // Pure display helpers called by descendant renders must see the same locale.
  activeLanguage = language;
  const setLanguage = useCallback((next: Language) => {
    if (!validLanguage(next)) return;
    activeLanguage = next;
    updateLanguage(next);
    const url = new URL(window.location.href);
    url.searchParams.set("lang", next);
    window.history.replaceState(window.history.state, "", url);
  }, []);

  useEffect(() => {
    document.documentElement.lang = language;
    document.title = language === "en" ? "Retinue · Task workspace" : "Retinue · 任务工作台";
    try {
      window.localStorage.setItem(STORAGE_KEY, language);
    } catch {
      // Saving a display preference is best-effort.
    }
  }, [language]);

  useEffect(() => {
    const syncLanguage = () => updateLanguage(readLanguage());
    window.addEventListener("popstate", syncLanguage);
    return () => window.removeEventListener("popstate", syncLanguage);
  }, []);

  const value = useMemo<I18nContextValue>(() => ({
    language,
    setLanguage,
    t: (source, values) => translate(language, source, values),
  }), [language, setLanguage]);
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nContextValue {
  return useContext(I18nContext);
}

export function LanguageSwitcher() {
  const { language, setLanguage, t: label } = useI18n();
  return <label className="language-switcher">
    <span>{label("语言")}</span>
    <select aria-label="Language / 语言" value={language} onChange={event => setLanguage(event.target.value as Language)}>
      <option value="zh-CN">简体中文</option>
      <option value="en">English</option>
    </select>
  </label>;
}
