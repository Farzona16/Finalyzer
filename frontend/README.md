# Finalyzer frontend

React 18 + TypeScript + Vite 8, Tailwind CSS v4, Zustand, TanStack Query, react-i18next, ECharts.

| Command | Purpose |
| --- | --- |
| `npm run dev` | Dev server on :5173; `/api` is proxied to the backend on :8000 |
| `npm run typecheck` | `tsc -b` (strict) |
| `npm run lint` | ESLint with the security plugin; raw HTML assignment is banned |
| `npm test` | Vitest (formatters vs. `shared/format_cases.json`, i18n catalogs, API client, app) |
| `npm run build` | Typecheck and production build into `dist/` (served by the backend) |

Rules worth knowing:

- All visible text lives in `src/i18n/{uz,en}.json`; both files need the same keys and placeholders (`{name}`), and every key must be used. Uzbek uses `oʻ`/`gʻ` (U+02BB) and an ASCII `'` for the tutuq.
- Numbers, money and dates go through `src/lib/format.ts`, which must agree with `core/i18n/formats.py`.
- No inline scripts (CSP `default-src 'self'`): the theme is set by `public/theme-init.js`.
