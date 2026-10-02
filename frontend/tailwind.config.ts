import type { Config } from 'tailwindcss'

// Colors come from the CSS variables in src/index.css (spec 9.1), so a theme switch
// never needs a rebuild. Tailwind v4 loads this file through `@config` in index.css.
const config: Config = {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: 'var(--bg)',
        surface: 'var(--surface)',
        'surface-2': 'var(--surface-2)',
        border: 'var(--border)',
        text: 'var(--text)',
        muted: 'var(--muted)',
        green: 'var(--green)',
        blue: 'var(--blue)',
        amber: 'var(--amber)',
        teal: 'var(--teal)',
        red: 'var(--red)',
        purple: 'var(--purple)',
        ring: 'var(--blue)',
      },
      borderRadius: { card: '16px' },
      fontFamily: {
        sans: ['"Inter Variable"', 'system-ui', 'Segoe UI', 'Roboto', 'sans-serif'],
      },
      boxShadow: {
        glow: '0 10px 15px -3px color-mix(in srgb, var(--blue) 5%, transparent)',
      },
      transitionDuration: { DEFAULT: '200ms' },
      transitionTimingFunction: { DEFAULT: 'ease-in-out' },
    },
  },
}

export default config
