/** Split glossary tokens of the form `[[key|visible text]]` (same as the backend jargon lint). */

export const TOKEN_RE = /(\[\[[^|\]]+\|[^\]]+\]\])/g
