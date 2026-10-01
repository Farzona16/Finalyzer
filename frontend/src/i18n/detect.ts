/**
 * Which language to show (spec 5.1): user profile, then localStorage, then the browser's
 * Accept-Language list, then Uzbek.
 */

export type Language = 'uz' | 'en'
export const LANGUAGES: readonly Language[] = ['uz', 'en']
export const DEFAULT_LANGUAGE: Language = 'uz'
export const LANGUAGE_STORAGE_KEY = 'fz_lang'

export function isLanguage(value: unknown): value is Language {
  return value === 'uz' || value === 'en'
}

/** The best supported language of a list such as `navigator.languages` (first match wins). */
export function fromBrowser(languages: readonly string[]): Language | null {
  for (const tag of languages) {
    const base = tag.toLowerCase().split('-')[0]
    if (isLanguage(base)) return base
  }
  return null
}

export function readStored(): Language | null {
  try {
    const value = localStorage.getItem(LANGUAGE_STORAGE_KEY)
    return isLanguage(value) ? value : null
  } catch {
    return null
  }
}

export function storeLanguage(language: Language): void {
  try {
    localStorage.setItem(LANGUAGE_STORAGE_KEY, language)
  } catch {
    // Storage may be blocked; the choice then lasts until the page closes.
  }
}

export function detectLanguage(profile?: Language | null): Language {
  if (profile && isLanguage(profile)) return profile
  const stored = readStored()
  if (stored) return stored
  const browser = typeof navigator === 'undefined' ? null : fromBrowser(navigator.languages ?? [])
  return browser ?? DEFAULT_LANGUAGE
}
