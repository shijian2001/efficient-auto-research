import { createContext, useContext, useEffect, useState } from 'react';

const STORAGE_KEY = 'arbor-lang';
const LangContext = createContext({ lang: 'en', setLang: () => {} });

function initialLang() {
  if (typeof window === 'undefined') return 'en';
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved === 'en' || saved === 'zh') return saved;
  } catch {
    /* ignore */
  }
  // Default to English; respect an explicit toggle saved in localStorage.
  return 'en';
}

export function LangProvider({ children }) {
  const [lang, setLangState] = useState(initialLang);

  // Persist only on an explicit toggle, so a fresh visitor always gets the
  // English default instead of a navigator-derived value sticking in storage.
  const setLang = (next) => {
    setLangState(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* ignore */
    }
  };

  useEffect(() => {
    document.documentElement.lang = lang === 'zh' ? 'zh-CN' : 'en';
  }, [lang]);

  return <LangContext.Provider value={{ lang, setLang }}>{children}</LangContext.Provider>;
}

export function useLang() {
  return useContext(LangContext);
}
