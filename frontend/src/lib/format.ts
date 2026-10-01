/**
 * Number, date and currency formatting for Uzbek and English (spec 5.3).
 *
 * This is the TypeScript twin of `core/i18n/formats.py`; both are tested against
 * `shared/format_cases.json`. Rounding is "half up" on the shortest decimal text of a number
 * (1.005 gives 1.01), so there is no floating point arithmetic in here: numbers are handled as
 * strings of digits.
 */

export type Locale = 'uz' | 'en'
export type Currency = 'UZS' | 'USD' | 'EUR' | 'RUB'
export type DateStyle = 'date' | 'month' | 'short'

const NNBSP = '\u202f' // digit groups in Uzbek
const NBSP = '\u00a0' // between a number and its unit
export const EMPTY = '\u2013' // shown for a missing value
const MINUS = '-'

const MONTHS: Record<Locale, readonly string[]> = {
  uz: ['yanvar', 'fevral', 'mart', 'aprel', 'may', 'iyun', 'iyul', 'avgust', 'sentabr', 'oktabr', 'noyabr', 'dekabr'],
  en: ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'],
}
const MONTHS_SHORT_EN = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'] as const
const WEEKDAYS: Record<Locale, readonly string[]> = {
  uz: ['dushanba', 'seshanba', 'chorshanba', 'payshanba', 'juma', 'shanba', 'yakshanba'],
  en: ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'],
}
const COMPACT_UNITS = [
  { uz: 'ming', en: 'K' },
  { uz: 'mln', en: 'M' },
  { uz: 'mlrd', en: 'B' },
  { uz: 'trln', en: 'T' },
] as const
const CURRENCIES: Record<Currency, { decimals: number; uz: string; en: string }> = {
  UZS: { decimals: 0, uz: 'so\u02bbm', en: 'UZS' },
  USD: { decimals: 2, uz: '$', en: '$' },
  EUR: { decimals: 2, uz: '\u20ac', en: '\u20ac' },
  RUB: { decimals: 2, uz: '\u20bd', en: '\u20bd' },
}

// ---------------------------------------------------------------- decimal text arithmetic

/** A non-negative decimal as digit strings plus a sign. `int` has no leading zeros. */
interface Dec {
  neg: boolean
  int: string
  frac: string
}

function normalize(neg: boolean, int: string, frac: string): Dec {
  const trimmed = int.replace(/^0+(?=\d)/, '')
  return { neg, int: trimmed === '' ? '0' : trimmed, frac }
}

function isZero(d: Dec): boolean {
  return /^0*$/.test(d.int + d.frac)
}

/** Multiply by 10^places (negative places divide). */
function shift(d: Dec, places: number): Dec {
  let all = d.int + d.frac
  let point = d.int.length + places
  if (point < 1) {
    all = '0'.repeat(1 - point) + all
    point = 1
  }
  if (point > all.length) all += '0'.repeat(point - all.length)
  return normalize(d.neg, all.slice(0, point), all.slice(point))
}

function parseDec(value: unknown): Dec | null {
  let text: string
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) return null
    text = String(value)
  } else if (typeof value === 'string') {
    text = value.trim()
  } else {
    return null
  }
  // The groups are separated by literal characters, so backtracking stays linear.
  // eslint-disable-next-line security/detect-unsafe-regex
  const match = /^([+-]?)(\d*)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/.exec(text)
  if (!match || (match[2] === '' && (match[3] ?? '') === '')) return null
  const dec = normalize(match[1] === '-', match[2] || '0', match[3] ?? '')
  return match[4] ? shift(dec, parseInt(match[4], 10)) : dec
}

function increment(digits: string): string {
  const chars = digits.split('')
  for (let i = chars.length - 1; i >= 0; i--) {
    if (chars[i] === '9') {
      chars[i] = '0'
    } else {
      chars[i] = String(Number(chars[i]) + 1)
      return chars.join('')
    }
  }
  return '1' + chars.join('')
}

/** Round half up (away from zero on the magnitude) to a fixed number of decimals. */
function round(d: Dec, digits: number): Dec {
  if (d.frac.length <= digits) return { ...d, frac: d.frac.padEnd(digits, '0') }
  let all = d.int + d.frac.slice(0, digits)
  if (d.frac.charCodeAt(digits) >= 53) all = increment(all)
  const result = normalize(d.neg, all.slice(0, all.length - digits), all.slice(all.length - digits))
  return isZero(result) ? { ...result, neg: false } : result
}

function group(digits: string, separator: string): string {
  // Applied to digit runs of at most a few dozen characters.
  // eslint-disable-next-line security/detect-unsafe-regex
  return digits.replace(/\B(?=(\d{3})+(?!\d))/g, separator)
}

function plain(d: Dec, digits: number, locale: Locale, trim = false): string {
  const rounded = round(d, digits)
  let fraction = rounded.frac
  if (trim) fraction = fraction.replace(/0+$/, '')
  const [groupMark, decimalMark] = locale === 'uz' ? [NNBSP, ','] : [',', '.']
  const body = group(rounded.int, groupMark) + (fraction ? decimalMark + fraction : '')
  return rounded.neg ? MINUS + body : body
}

// ---------------------------------------------------------------- numbers

export function formatInteger(value: unknown, locale: Locale = 'uz'): string {
  const d = parseDec(value)
  return d ? plain(d, 0, locale) : EMPTY
}

export function formatDecimal(value: unknown, locale: Locale = 'uz', digits = 2): string {
  const d = parseDec(value)
  return d ? plain(d, digits, locale) : EMPTY
}

export function formatPercent(
  value: unknown,
  locale: Locale = 'uz',
  options: { digits?: number; from_ratio?: boolean } = {},
): string {
  const d = parseDec(value)
  if (!d) return EMPTY
  const scaled = options.from_ratio ? shift(d, 2) : d
  return plain(scaled, options.digits ?? 1, locale) + '%'
}

export function formatCompact(value: unknown, locale: Locale = 'uz', options: { digits?: number } = {}): string {
  const digits = options.digits ?? 1
  const d = parseDec(value)
  if (!d) return EMPTY
  const size: Dec = { ...d, neg: false }
  let text: string
  let unit: (typeof COMPACT_UNITS)[number] | null = null
  if (round(size, digits).int.length <= 3) {
    text = plain(size, digits, locale, true)
  } else {
    // The largest unit that fits; rounding may push the number into the next unit.
    let index = 0
    for (let k = 1; k <= COMPACT_UNITS.length; k++) {
      if (size.int.length >= 3 * k + 1) index = k - 1
    }
    let scaled = round(shift(size, -3 * (index + 1)), digits)
    while (scaled.int.length >= 4 && index < COMPACT_UNITS.length - 1) {
      index += 1
      scaled = round(shift(size, -3 * (index + 1)), digits)
    }
    text = plain(scaled, digits, locale, true)
    unit = COMPACT_UNITS[index] ?? null
  }
  const sign = d.neg && !isZero(d) && text !== '0' ? MINUS : ''
  if (!unit) return sign + text
  return locale === 'uz' ? `${sign}${text}${NBSP}${unit.uz}` : `${sign}${text}${unit.en}`
}

export function formatCurrency(
  value: unknown,
  locale: Locale = 'uz',
  options: { currency?: Currency; compact?: boolean; digits?: number } = {},
): string {
  const currency = options.currency ?? 'UZS'
  const spec = CURRENCIES[currency]
  if (!spec) throw new Error(`unsupported currency: ${String(currency)}`)
  const d = parseDec(value)
  if (!d) return EMPTY
  const compact = options.compact ?? true
  let amount = compact ? formatCompact(value, locale) : plain(d, options.digits ?? spec.decimals, locale)
  const negative = amount.startsWith(MINUS)
  if (negative) amount = amount.slice(1)
  let result: string
  if (locale === 'uz') result = `${amount}${NBSP}${spec.uz}`
  else if (currency === 'UZS') result = `${spec.en}${NBSP}${amount}`
  else result = `${spec.en}${amount}`
  return negative ? MINUS + result : result
}

// ---------------------------------------------------------------- names and dates

export function monthName(month: number, locale: Locale = 'uz', short = false): string {
  if (!Number.isInteger(month) || month < 1 || month > 12) throw new RangeError('month must be 1-12')
  if (locale === 'en' && short) return MONTHS_SHORT_EN[month - 1] as string
  return MONTHS[locale][month - 1] as string
}

/** Monday = 0, like Python's `date.weekday()`. */
export function weekdayName(weekday: number, locale: Locale = 'uz'): string {
  if (!Number.isInteger(weekday) || weekday < 0 || weekday > 6) throw new RangeError('weekday must be 0-6')
  return WEEKDAYS[locale][weekday] as string
}

interface Ymd {
  year: number
  month: number
  day: number
}

function toYmd(value: unknown): Ymd | null {
  if (value instanceof Date) {
    if (Number.isNaN(value.getTime())) return null
    return { year: value.getFullYear(), month: value.getMonth() + 1, day: value.getDate() }
  }
  if (typeof value !== 'string') return null
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value)
  if (!match) return null
  const [year, month, day] = [Number(match[1]), Number(match[2]), Number(match[3])]
  const probe = new Date(Date.UTC(year, month - 1, day))
  const valid = probe.getUTCFullYear() === year && probe.getUTCMonth() === month - 1 && probe.getUTCDate() === day
  return valid ? { year, month, day } : null
}

export function formatDate(value: unknown, locale: Locale = 'uz', style: DateStyle = 'date'): string {
  const ymd = toYmd(value)
  if (!ymd) return EMPTY
  const { year, month, day } = ymd
  const two = (n: number) => String(n).padStart(2, '0')
  if (style === 'date') {
    return locale === 'uz'
      ? `${year}-yil ${day}-${monthName(month, 'uz')}`
      : `${day} ${monthName(month, 'en', true)} ${year}`
  }
  if (style === 'month') {
    return locale === 'uz' ? `${year}-yil ${monthName(month, 'uz')}` : `${monthName(month, 'en', true)} ${year}`
  }
  return locale === 'uz' ? `${two(day)}.${two(month)}.${year}` : `${year}-${two(month)}-${two(day)}`
}

// ---------------------------------------------------------------- parsing

const UNIT_FACTORS: Record<string, number> = {
  ming: 3, mln: 6, mlrd: 9, trln: 12, k: 3, m: 6, b: 9, t: 12, тыс: 3, млн: 6, млрд: 9, трлн: 12,
}
const CURRENCY_WORDS = /(so[\u02bb'\u2018\u2019`\u02bc]?m|uzs|usd|eur|rub|сум|[$\u20ac\u20bd])/gi
const SPACES = /[\s\u00a0\u202f\u2009\u2007]+/g
const UNIT_TAIL = /(ming|mlrd|mln|trln|тыс|млрд|млн|трлн|[kmbt])\.?$/

/** Read a number written the way the formatters write it; `null` when it is not a number. */
export function parseNumber(text: string, locale: Locale = 'uz'): number | null {
  if (typeof text !== 'string') return null
  const cleaned = text.trim().toLowerCase().replace(CURRENCY_WORDS, ' ').replace(/\u2212/g, '-').replace(/%/g, ' ').trim()
  const squeezed = cleaned.replace(SPACES, '')
  const unit = UNIT_TAIL.exec(squeezed)
  const places = unit ? (UNIT_FACTORS[unit[1] as string] ?? 0) : 0
  let core = unit ? squeezed.replace(UNIT_TAIL, '') : squeezed
  const negative = core.startsWith('-')
  core = core.replace(/^[+-]+/, '')
  if (!core || !/^[\d.,]+$/.test(core) || !/\d/.test(core)) return null
  const commas = (core.match(/,/g) ?? []).length
  const dots = (core.match(/\./g) ?? []).length
  if (commas && dots) {
    const decimalMark = core.lastIndexOf(',') > core.lastIndexOf('.') ? ',' : '.'
    const groupMark = decimalMark === ',' ? '.' : ','
    core = core.split(groupMark).join('').replace(decimalMark, '.')
  } else if (commas || dots) {
    const mark = commas ? ',' : '.'
    const count = commas || dots
    const head = core.slice(0, core.lastIndexOf(mark))
    const tail = core.slice(core.lastIndexOf(mark) + 1)
    if (count > 1) {
      if (!core.split(mark).slice(1).every((part) => part.length === 3)) return null
      core = core.split(mark).join('')
    } else if (locale === 'en' && mark === ',' && tail.length === 3 && head.length >= 1 && head.length <= 3) {
      core = head + tail
    } else {
      core = head + '.' + tail
    }
  }
  const dec = parseDec(core)
  if (!dec) return null
  const scaled = shift(dec, places)
  const value = Number(`${scaled.int}.${scaled.frac || '0'}`)
  return value === 0 ? 0 : negative ? -value : value
}
