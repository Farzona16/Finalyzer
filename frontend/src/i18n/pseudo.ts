/**
 * The `pseudo` locale (spec 5.1): every English string with accents, 35% longer and in
 * brackets. A layout that survives it will survive Uzbek, which is often longer than English.
 */

const ACCENTS: Record<string, string> = {
  a: 'á', e: 'é', i: 'í', o: 'ó', u: 'ú', y: 'ý', c: 'ç', n: 'ñ',
  A: 'Á', E: 'É', I: 'Í', O: 'Ó', U: 'Ú', Y: 'Ý', C: 'Ç', N: 'Ñ',
}
const PLACEHOLDER = /(\{[A-Za-z0-9_]+\})/

export function pseudoize(text: string): string {
  const pieces = text.split(PLACEHOLDER)
  const accented = pieces
    .map((piece) => (PLACEHOLDER.test(piece) ? piece : piece.replace(/[A-Za-z]/g, (c) => ACCENTS[c] ?? c)))
    .join('')
  const padding = '~'.repeat(Math.ceil(text.length * 0.35))
  return `[${accented}${padding}]`
}

export function pseudoCatalog(catalog: Record<string, string>): Record<string, string> {
  return Object.fromEntries(Object.entries(catalog).map(([key, value]) => [key, pseudoize(value)]))
}
