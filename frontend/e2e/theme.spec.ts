import { expect, test } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const init = readFileSync(join(here, '../public/theme-init.js'), 'utf8')

test('theme-init.js sets data-theme before the first paint', async ({ page }) => {
  const html = `<!doctype html><html lang="uz" data-theme="dark"><head>
    <script src="/theme-init.js"></script>
  </head><body><h1>probe</h1></body></html>`
  await page.route('http://theme.test/**', async (route) => {
    const url = route.request().url()
    if (url.endsWith('/theme-init.js')) {
      await route.fulfill({ contentType: 'application/javascript', body: init })
      return
    }
    await route.fulfill({ contentType: 'text/html', body: html })
  })
  await page.addInitScript(() => {
    localStorage.setItem('fz_theme', 'light')
    localStorage.setItem('fz_lang', 'en')
  })
  await page.goto('http://theme.test/')
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
  await expect(page.locator('html')).toHaveAttribute('lang', 'en')
})
