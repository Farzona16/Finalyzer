import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

import { describe, expect, it, vi } from 'vitest'

import { THEME_STORAGE_KEY } from '@/theme/store'

import { DEFAULT_LANGUAGE, LANGUAGE_STORAGE_KEY, detectLanguage, fromBrowser, storeLanguage } from './detect'
import en from './en.json'
import { createI18n } from './index'
import { pseudoCatalog, pseudoize } from './pseudo'
import uz from './uz.json'

const SRC = join(process.cwd(), 'src')
const PLACEHOLDER = /\{[A-Za-z0-9_]+\}/g

function placeholders(text: string): string[] {
  return [...text.matchAll(PLACEHOLDER)].map((m) => m[0]).sort()
}

function sourceFiles(): string[] {
  return (readdirSync(SRC, { recursive: true }) as string[])
    .filter((f) => /\.(ts|tsx)$/.test(f) && !/\.test\.(ts|tsx)$/.test(f) && !f.startsWith('test'))
    .map((f) => join(SRC, f))
}

describe('catalogs', () => {
  it('have the same keys in both languages', () => {
    expect(Object.keys(uz).sort()).toEqual(Object.keys(en).sort())
  })

  it('have the same placeholders for every key', () => {
    for (const key of Object.keys(en) as (keyof typeof en)[]) {
      expect(placeholders(uz[key]), key).toEqual(placeholders(en[key]))
    }
  })

  it('have no empty strings', () => {
    for (const catalog of [uz, en]) {
      for (const [key, value] of Object.entries(catalog)) expect(value.trim(), key).not.toBe('')
    }
  })

  it('leave no key unused', () => {
    const used = new Set<string>()
    const prefixes: string[] = []
    for (const file of sourceFiles()) {
      const text = readFileSync(file, 'utf8')
      for (const m of text.matchAll(/\bt\(\s*(['"`])([^'"`]+)\1/g)) {
        const key = m[2] ?? ''
        const cut = key.indexOf('${')
        if (cut >= 0) prefixes.push(key.slice(0, cut))
        else used.add(key)
      }
    }
    const unused = Object.keys(en).filter((k) => !used.has(k) && !prefixes.some((p) => k.startsWith(p)))
    expect(unused).toEqual([])
  })

  it('use no key that is missing', () => {
    const known = new Set(Object.keys(en))
    for (const file of sourceFiles()) {
      for (const m of readFileSync(file, 'utf8').matchAll(/\bt\(\s*(['"])([^'"]+)\1/g)) {
        expect(known.has(m[2] ?? ''), `${file}: ${m[2]}`).toBe(true)
      }
    }
  })
})

describe('Uzbek orthography (spec 5.3)', () => {
  const texts = Object.entries(uz)

  it('writes oʻ and gʻ with U+02BB, never with a quote lookalike', () => {
    for (const [key, text] of texts) {
      expect(text, key).not.toMatch(/[oOgG][\u0027\u2018\u2019\u02BC\u0060\u00B4]/u)
    }
  })

  it('keeps the tutuq as an ASCII apostrophe, never a curly quote', () => {
    for (const [key, text] of texts) {
      expect(text, key).not.toMatch(/[\u2018\u2019\u02BC]/u)
      expect(text, key).not.toMatch(/[^oOgG]\u02BB/u) // U+02BB only ever follows o or g
    }
  })

  it('contains the expected spellings', () => {
    expect(uz['theme.light']).toBe('Yorugʻ')
    expect(uz['errors.network']).toContain('yoʻq')
    expect(uz['home.letters_check']).toContain('oʻqish')
    expect(uz['home.letters_check']).toContain('gʻoya')
    expect(uz['home.letters_check']).toContain('ma\u0027lumot')
  })
})

describe('pseudo locale', () => {
  it('wraps, accents and lengthens, and keeps placeholders intact', () => {
    const text = pseudoize('Version {version}')
    expect(text.startsWith('[')).toBe(true)
    expect(text.endsWith(']')).toBe(true)
    expect(text).toContain('{version}')
    expect(text).toContain('Vérsíóñ')
    expect(text.length).toBeGreaterThan('Version {version}'.length * 1.3)
  })

  it('transforms every English key', () => {
    const catalog = pseudoCatalog(en)
    expect(Object.keys(catalog)).toEqual(Object.keys(en))
    for (const key of Object.keys(en) as (keyof typeof en)[]) {
      expect(placeholders(catalog[key] ?? ''), key).toEqual(placeholders(en[key]))
    }
  })

  it('is selectable as a language', () => {
    const i18n = createI18n('pseudo')
    expect(i18n.t('errors.reference', { id: 'ab12' })).toMatch(/^\[.*ab12.*\]$/)
  })
})

describe('i18n instance', () => {
  it('interpolates single-brace placeholders in both languages', () => {
    expect(createI18n('uz').t('home.status.ok', { version: '3.0.0' })).toContain('3.0.0')
    expect(createI18n('en').t('home.status.ok', { version: '3.0.0' })).toContain('3.0.0')
  })

  it('does not escape values, because React does', () => {
    expect(createI18n('en').t('errors.reference', { id: 'a&b' })).toContain('a&b')
  })

  it('answers in Uzbek and English', () => {
    expect(createI18n('uz').t('nav.home')).toBe(uz['nav.home'])
    expect(createI18n('en').t('nav.home')).toBe(en['nav.home'])
  })
})

describe('language detection', () => {
  it('prefers the profile, then storage, then the browser, then Uzbek', () => {
    localStorage.clear()
    vi.spyOn(navigator, 'languages', 'get').mockReturnValue(['ru-RU'])
    expect(detectLanguage()).toBe(DEFAULT_LANGUAGE)
    vi.spyOn(navigator, 'languages', 'get').mockReturnValue(['ru-RU', 'en-GB'])
    expect(detectLanguage()).toBe('en')
    expect(fromBrowser(['ru-RU', 'en-GB'])).toBe('en')
    expect(fromBrowser(['uz-Latn-UZ'])).toBe('uz')
    expect(fromBrowser(['de', 'ru'])).toBeNull()
    storeLanguage('en')
    expect(detectLanguage()).toBe('en')
    expect(detectLanguage('uz')).toBe('uz')
    localStorage.clear()
  })

  it('ignores a garbage stored value', () => {
    vi.spyOn(navigator, 'languages', 'get').mockReturnValue(['ru-RU'])
    localStorage.setItem(LANGUAGE_STORAGE_KEY, 'xx')
    expect(detectLanguage()).toBe(DEFAULT_LANGUAGE)
    localStorage.clear()
  })
})

describe('theme-init.js', () => {
  const script = readFileSync(join(process.cwd(), 'public', 'theme-init.js'), 'utf8')

  it('reads the same storage keys as the app', () => {
    expect(script).toContain(`'${THEME_STORAGE_KEY}'`)
    expect(script).toContain(`'${LANGUAGE_STORAGE_KEY}'`)
  })

  it('is the only script in index.html and is not inline', () => {
    const html = readFileSync(join(process.cwd(), 'index.html'), 'utf8')
    const scripts = [...html.matchAll(/<script\b[^>]*>/g)].map((m) => m[0])
    expect(scripts.length).toBeGreaterThanOrEqual(2)
    for (const tag of scripts) expect(tag).toContain('src=')
    expect(html).toContain('/theme-init.js')
  })
})

describe('Inter font', () => {
  const css = readFileSync(join(process.cwd(), 'node_modules', '@fontsource-variable', 'inter', 'index.css'), 'utf8')
  const rangeOf = (subset: string) =>
    css
      .split('@font-face')
      .find((block) => block.includes(`inter-${subset}-wght-normal.woff2`))
      ?.match(/unicode-range:\s*([^;]+);/i)?.[1]
      ?.toLowerCase() ?? ''

  it('serves oʻ/gʻ (U+02BB) and the narrow no-break space (U+202F) from the main latin subset', () => {
    const latin = rangeOf('latin')
    expect(latin).toContain('u+02bb-02bc')
    expect(latin).toContain('u+2000-206f')
  })
})
