import { createContext, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { STRINGS, LANGS } from "./strings";
import type { Lang, StringKey } from "./strings";

interface Ctx {
  lang: Lang;
  setLang: (l: Lang) => void;
  t: (key: StringKey, params?: Record<string, string | number>) => string;
  suggestions: readonly string[];
}

const I18nContext = createContext<Ctx | null>(null);

const STORAGE_KEY = "taleforge.lang";

function detect(): Lang {
  const saved = (typeof window !== "undefined" && window.localStorage.getItem(STORAGE_KEY)) as Lang | null;
  if (saved === "en" || saved === "ru") return saved;
  if (typeof navigator !== "undefined") {
    const tag = (navigator.language || "en").toLowerCase();
    if (tag.startsWith("ru")) return "ru";
  }
  return "en";
}

function format(template: string, params?: Record<string, string | number>): string {
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (_, k) =>
    params[k] !== undefined ? String(params[k]) : `{${k}}`
  );
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(detect);

  useEffect(() => {
    if (typeof window !== "undefined") {
      window.localStorage.setItem(STORAGE_KEY, lang);
      document.documentElement.setAttribute("lang", lang);
    }
  }, [lang]);

  const setLang = (l: Lang) => setLangState(l);

  const t = (key: StringKey, params?: Record<string, string | number>) => {
    const entry = STRINGS[key];
    if (!entry) return String(key);
    // suggestions is an array entry; t() callers should use `suggestions` instead.
    if (Array.isArray((entry as any).en)) return String(key);
    const raw = (entry as Record<Lang, string>)[lang] ?? (entry as Record<Lang, string>).en;
    return format(raw, params);
  };

  const suggestions = (STRINGS["suggestions"] as Record<Lang, readonly string[]>)[lang]
    ?? (STRINGS["suggestions"] as Record<Lang, readonly string[]>).en;

  return (
    <I18nContext.Provider value={{ lang, setLang, t, suggestions }}>
      {children}
    </I18nContext.Provider>
  );
}

export function useI18n(): Ctx {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error("useI18n must be used inside <I18nProvider>");
  return ctx;
}

export { LANGS };
export type { Lang };
