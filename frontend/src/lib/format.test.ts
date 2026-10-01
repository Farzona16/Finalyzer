import { describe, expect, it } from 'vitest'

import fixture from '@shared/format_cases.json'
import {
  EMPTY,
  formatCompact,
  formatCurrency,
  formatDate,
  formatDecimal,
  formatInteger,
  formatPercent,
  monthName,
  parseNumber,
  weekdayName,
  type Currency,
  type DateStyle,
  type Locale,
} from './format'

interface Case {
  fn: string
  locale: Locale
  value: unknown
  args: Record<string, unknown>
  expected: string | number | null
}

const cases = fixture.cases as unknown as Case[]

function run(c: Case): unknown {
  const a = c.args
  switch (c.fn) {
    case 'integer':
      return formatInteger(c.value, c.locale)
    case 'decimal':
      return formatDecimal(c.value, c.locale, a.digits as number | undefined)
    case 'percent':
      return formatPercent(c.value, c.locale, { digits: a.digits as number | undefined, from_ratio: a.from_ratio as boolean | undefined })
    case 'compact':
      return formatCompact(c.value, c.locale, { digits: a.digits as number | undefined })
    case 'currency':
      return formatCurrency(c.value, c.locale, { currency: a.currency as Currency, compact: a.compact as boolean | undefined })
    case 'month':
      return monthName(c.value as number, c.locale)
    case 'weekday':
      return weekdayName(c.value as number, c.locale)
    case 'date':
      return formatDate(c.value, c.locale, a.style as DateStyle)
    case 'parse':
      return parseNumber(c.value as string, c.locale)
    default:
      throw new Error(`unknown function ${c.fn}`)
  }
}

describe('shared format cases (the same file the Python tests use)', () => {
  it('has enough cases', () => {
    expect(cases.length).toBeGreaterThanOrEqual(80)
  })

  it.each(cases.map((c, i) => [i, `${c.fn} ${c.locale} ${JSON.stringify(c.value)} ${JSON.stringify(c.args)}`, c] as const))(
    'case %i: %s',
    (_index, _label, c) => {
      expect(run(c)).toBe(c.expected)
    },
  )
})

describe('properties', () => {
  it('rounds half up on the decimal text, not on the binary float', () => {
    expect(formatDecimal(1.005, 'en')).toBe('1.01')
    expect(formatDecimal(0.125, 'en')).toBe('0.13')
    expect(formatDecimal(-2.5, 'en', 0)).toBe('-3')
  })

  it('handles exponent notation and very large numbers', () => {
    expect(formatInteger(1e21, 'en')).toBe('1,000,000,000,000,000,000,000')
    expect(formatDecimal(1e-7, 'en', 8)).toBe('0.00000010')
    expect(formatCompact(1.5e15, 'en')).toBe('1,500T')
  })

  it('never shows a negative zero', () => {
    expect(formatDecimal(-0.0001, 'uz')).toBe('0,00')
    expect(formatPercent(-0.01, 'en')).toBe('0.0%')
    expect(formatCompact(-0.04, 'en')).toBe('0')
  })

  it('shows a dash for missing and invalid values', () => {
    for (const value of [null, undefined, Number.NaN, Infinity, 'abc', true, {}]) {
      expect(formatInteger(value, 'uz')).toBe(EMPTY)
      expect(formatCompact(value, 'en')).toBe(EMPTY)
    }
  })

  it('formats Date objects with local calendar parts', () => {
    expect(formatDate(new Date(2026, 7, 12, 23, 59), 'uz')).toBe('2026-yil 12-avgust')
    expect(formatDate(new Date('invalid'), 'uz')).toBe(EMPTY)
  })

  it('rejects impossible calendar dates', () => {
    expect(formatDate('2026-02-30', 'en')).toBe(EMPTY)
    expect(formatDate('2026-13-01', 'en')).toBe(EMPTY)
  })

  it('throws on out-of-range month and weekday', () => {
    expect(() => monthName(0, 'uz')).toThrow(RangeError)
    expect(() => weekdayName(7, 'en')).toThrow(RangeError)
  })

  it('writes the Uzbek currency word with the right letter', () => {
    expect(formatCurrency(5000, 'uz').endsWith('so\u02bbm')).toBe(true)
  })

  it('round-trips through the parser', () => {
    for (const locale of ['uz', 'en'] as const) {
      for (const value of [0, 7, 1234, 1234567, -98765, 12.5, 1234.56, -0.5]) {
        expect(parseNumber(formatDecimal(value, locale), locale)).toBe(value)
      }
      for (const value of [5000, 12_800_000, 1_200_000_000]) {
        expect(parseNumber(formatCompact(value, locale), locale)).toBe(value)
        expect(parseNumber(formatCurrency(value, locale, { currency: 'USD' }), locale)).toBe(value)
      }
    }
  })

  it('never throws on garbage', () => {
    for (const text of ['', ' ', '--5', '1..2', '1,,2', 'e10', 'NaN', 'inf', '1e5', '%', 'mln', '١٢٣']) {
      expect(() => parseNumber(text, 'uz')).not.toThrow()
      expect(() => parseNumber(text, 'en')).not.toThrow()
    }
  })
})
