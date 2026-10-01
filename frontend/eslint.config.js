import js from '@eslint/js'
import reactHooks from 'eslint-plugin-react-hooks'
import security from 'eslint-plugin-security'
import globals from 'globals'
import tseslint from 'typescript-eslint'

const NO_RAW_HTML = [
  {
    selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']",
    message: 'Raw HTML is banned (spec S-XSS). Render text through React.',
  },
  {
    selector: "AssignmentExpression[left.property.name=/^(innerHTML|outerHTML)$/]",
    message: 'Assigning HTML is banned. Use text nodes.',
  },
]

export default tseslint.config(
  { ignores: ['dist', 'coverage', 'node_modules', 'e2e', 'playwright.config.ts', 'src/api/schema.ts', 'src/api/openapi.json'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  security.configs.recommended,
  {
    files: ['src/**/*.{ts,tsx}'],
    languageOptions: { globals: globals.browser },
    plugins: { 'react-hooks': reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'no-restricted-syntax': ['error', ...NO_RAW_HTML],
      'no-eval': 'error',
      'no-implied-eval': 'error',
      'no-new-func': 'error',
      // Too many false positives on typed lookup tables; indexing is covered by
      // noUncheckedIndexedAccess and the tests.
      'security/detect-object-injection': 'off',
    },
  },
  {
    files: ['public/**/*.js'],
    languageOptions: { globals: globals.browser },
  },
  {
    files: ['*.config.{js,ts}', 'vite.config.ts'],
    languageOptions: { globals: globals.node },
  },
  {
    files: ['**/*.test.{ts,tsx}', 'src/test/**'],
    languageOptions: { globals: { ...globals.node, ...globals.browser } },
    rules: { 'security/detect-non-literal-fs-filename': 'off', 'security/detect-non-literal-regexp': 'off' },
  },
)
