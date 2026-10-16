import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import i18n from '../app/i18n';

type Theme = 'light' | 'dark';
type Lang  = 'en' | 'bn';

interface UIState {
  theme: Theme;
  lang:  Lang;
  toggleTheme: () => void;
  setLang: (lang: Lang) => void;
}

export const useUIStore = create<UIState>()(
  persist(
    (set) => ({
      theme: 'light',
      lang:  'en',
      toggleTheme: () =>
        set((s) => ({ theme: s.theme === 'light' ? 'dark' : 'light' })),
      setLang: (lang) => {
        i18n.changeLanguage(lang);
        localStorage.setItem('dhaka_lang', lang);
        document.documentElement.lang = lang;
        set({ lang });
      },
    }),
    { name: 'dhaka_ui', partialize: (s) => ({ theme: s.theme, lang: s.lang }) }
  )
);
