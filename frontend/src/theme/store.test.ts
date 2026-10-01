import { beforeEach, describe, expect, it, vi } from 'vitest'

import { THEME_STORAGE_KEY, resolveTheme, useThemeStore, watchSystemTheme } from './store'

function mockScheme(light: boolean) {
  const listeners = new Set<() => void>()
  vi.stubGlobal(
    'matchMedia',
    vi.fn(() => ({
      matches: light,
      addEventListener: (_: string, fn: () => void) => listeners.add(fn),
      removeEventListener: (_: string, fn: () => void) => listeners.delete(fn),
    })),
  )
  return listeners
}

describe('theme store', () => {
  beforeEach(() => {
    localStorage.clear()
    mockScheme(false)
  })

  it('resolves system to the operating system scheme', () => {
    mockScheme(true)
    expect(resolveTheme('system')).toBe('light')
    mockScheme(false)
    expect(resolveTheme('system')).toBe('dark')
    expect(resolveTheme('light')).toBe('light')
  })

  it('sets data-theme and remembers the choice', () => {
    useThemeStore.getState().setPreference('light')
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('light')
    useThemeStore.getState().setPreference('dark')
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
  })

  it('ignores an invalid preference', () => {
    useThemeStore.getState().setPreference('dark')
    useThemeStore.getState().setPreference('purple' as never)
    expect(useThemeStore.getState().preference).toBe('dark')
  })

  it('follows the operating system only while the preference is system', () => {
    const listeners = mockScheme(false)
    useThemeStore.getState().setPreference('system')
    const stop = watchSystemTheme()
    expect(listeners.size).toBe(1)
    mockScheme(true)
    listeners.forEach((fn) => fn())
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')
    useThemeStore.getState().setPreference('dark')
    listeners.forEach((fn) => fn())
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
    stop()
    expect(listeners.size).toBe(0)
  })
})
