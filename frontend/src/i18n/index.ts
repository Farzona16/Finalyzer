import i18next from 'i18next'
import { initReactI18next } from 'react-i18next'

import { detectLanguage, isLanguage, storeLanguage, type Language } from './detect'
import en from './en.json'
import { pseudoCatalog } from './pseudo'
import uz from './uz.json'

export { LANGUAGES, type Language } from './detect'
export const PSEUDO = 'pseudo'

/** Single braces, like the backend and the spec: `{version}`, not `{{version}}`. */
export function createI18n(initial: Language | typeof PSEUDO = detectLanguage()) {
  const instance = i18next.createInstance()
  void instance.use(initReactI18next).init({
    resources: {
      uz: { translation: uz },
      en: { translation: en },
      [PSEUDO]: { translation: pseudoCatalog(en) },
    },
    lng: initial,
    fallbackLng: 'uz',
    interpolation: { prefix: '{', suffix: '}', escapeValue: false }, // React escapes already
    returnNull: false,
    initAsync: false,
  })
  return instance
}

export const i18n = createI18n()

/** Switch the language of the whole interface at once and remember the choice. */
export async function setLanguage(language: Language): Promise<void> {
  if (!isLanguage(language)) return
  storeLanguage(language)
  document.documentElement.setAttribute('lang', language)
  await i18n.changeLanguage(language)
}

/** The current language for formatting; the pseudo locale formats like English. */
export function currentLocale(): Language {
  return i18n.language === 'uz' ? 'uz' : 'en'
}
