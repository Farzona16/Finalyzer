import { create } from 'zustand'

export type ThemePreference = 'light' | 'dark' | 'system'
export type ResolvedTheme = 'light' | 'dark'

/** Keep in sync with public/theme-init.js (a test compares them). */
export const THEME_STORAGE_KEY = 'fz_theme'
export const THEME_PREFERENCES: readonly ThemePreference[] = ['light', 'dark', 'system']

export function isThemePreference(value: unknown): value is ThemePreference {
  return value === 'light' || value === 'dark' || value === 'system'
}

function systemPrefersLight(): boolean {
  return typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-color-scheme: light)').matches
}

export function resolveTheme(preference: ThemePreference): ResolvedTheme {
  if (preference === 'system') return systemPrefersLight() ? 'light' : 'dark'
  return preference
}

export function defaultThemePreference(): ThemePreference {
  const coarse = typeof window !== 'undefined' && !!window.matchMedia?.('(pointer: coarse)').matches
  const lowMem =
    typeof navigator !== 'undefined' &&
    typeof (navigator as Navigator & { deviceMemory?: number }).deviceMemory === 'number' &&
    ((navigator as Navigator & { deviceMemory?: number }).deviceMemory ?? 8) <= 4
  return coarse || lowMem ? 'light' : 'system'
}

function readStored(): ThemePreference {
  try {
    const value = localStorage.getItem(THEME_STORAGE_KEY)
    return isThemePreference(value) ? value : defaultThemePreference()
  } catch {
    return defaultThemePreference()
  }
}

function apply(preference: ThemePreference): ResolvedTheme {
  const resolved = resolveTheme(preference)
  document.documentElement.setAttribute('data-theme', resolved)
  return resolved
}

interface ThemeState {
  preference: ThemePreference
  resolved: ResolvedTheme
  setPreference: (preference: ThemePreference) => void
  /** Re-apply after the operating system changes its colour scheme (only matters for `system`). */
  refresh: () => void
}

export const useThemeStore = create<ThemeState>((set, get) => {
  const preference = readStored()
  return {
    preference,
    resolved: apply(preference),
    setPreference: (next) => {
      if (!isThemePreference(next)) return
      try {
        localStorage.setItem(THEME_STORAGE_KEY, next)
      } catch {
        // Storage may be blocked; the choice then lasts until the page closes.
      }
      set({ preference: next, resolved: apply(next) })
    },
    refresh: () => set({ resolved: apply(get().preference) }),
  }
})

/** Follow the operating system while the preference is `system`. Returns the cleanup. */
export function watchSystemTheme(): () => void {
  const query = window.matchMedia?.('(prefers-color-scheme: light)')
  if (!query) return () => undefined
  const listener = () => useThemeStore.getState().refresh()
  query.addEventListener('change', listener)
  return () => query.removeEventListener('change', listener)
}
