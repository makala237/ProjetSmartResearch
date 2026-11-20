import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { type AppLang, TRANSLATIONS } from "./translations";

const STORAGE_KEY = "sr_default_lang";

interface LanguageContextValue {
  lang: AppLang;
  setLang: (next: AppLang) => void;
  t: (key: string, vars?: Record<string, string | number>) => string;
}

const LanguageContext = createContext<LanguageContextValue>({
  lang: "fr",
  setLang: () => undefined,
  t: (key) => TRANSLATIONS.fr[key] ?? key,
});

function readStoredLang(): AppLang {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw === "en") return "en";
  } catch {
    // Ignore storage errors
  }
  return "fr";
}

export function LanguageProvider({ children }: { children: React.ReactNode }) {
  const [lang, setLangState] = useState<AppLang>(readStoredLang);

  const setLang = useCallback((next: AppLang) => {
    const normalized: AppLang = next === "en" ? "en" : "fr";
    setLangState(normalized);
    try {
      localStorage.setItem(STORAGE_KEY, normalized);
    } catch {
      // Ignore storage errors
    }
  }, []);

  useEffect(() => {
    if (typeof document !== "undefined") {
      document.documentElement.lang = lang;
    }
  }, [lang]);

  const t = useCallback(
    (key: string, vars?: Record<string, string | number>) => {
      let template = TRANSLATIONS[lang]?.[key] ?? TRANSLATIONS.fr[key] ?? key;
      if (vars) {
        for (const [k, v] of Object.entries(vars)) {
          template = template.replace(new RegExp(`\\{${k}\\}`, "g"), String(v));
        }
      }
      return template;
    },
    [lang]
  );

  return (
    <LanguageContext.Provider value={{ lang, setLang, t }}>
      {children}
    </LanguageContext.Provider>
  );
}

export function useLanguage(): LanguageContextValue {
  return useContext(LanguageContext);
}
