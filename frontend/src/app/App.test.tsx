import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { resetApiState } from '@/api/client'
import { i18n, setLanguage } from '@/i18n'
import uz from '@/i18n/uz.json'
import en from '@/i18n/en.json'
import { useThemeStore } from '@/theme/store'

import { TestApp } from './App'

function serverUp(up: boolean) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      const path = String(url)
      if (!up) return Promise.reject(new TypeError('offline'))
      if (path.includes('/auth/me')) {
        return new Response(JSON.stringify({ code: 'not_authenticated', title: 'x', detail: 'x', status: 401 }), {
          status: 401,
          headers: { 'Content-Type': 'application/problem+json' },
        })
      }
      if (path.includes('/auth/csrf')) {
        return new Response(JSON.stringify({ csrf_token: 'tok' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      return new Response(JSON.stringify({ version: '3.0.0', first_run_required: true }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }),
  )
}

describe('app shell and home page', () => {
  beforeEach(async () => {
    localStorage.clear()
    resetApiState()
    vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener: () => undefined, removeEventListener: () => undefined }))
    await setLanguage('uz')
  })
  afterEach(() => localStorage.clear())

  it('renders in Uzbek by default with the server status', async () => {
    serverUp(true)
    render(<TestApp />)
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe(uz['home.title'])
    await waitFor(() => expect(screen.getByText(/versiya 3\.0\.0/)).toBeTruthy())
    expect(screen.getByText(uz['home.letters_check'])).toBeTruthy()
  })

  it('shows a plain message when the server is down', async () => {
    serverUp(false)
    render(<TestApp />)
    await waitFor(() => expect(screen.getByText(uz['home.status.down'])).toBeTruthy())
  })

  it('switches every visible string at once and remembers the language', async () => {
    serverUp(true)
    render(<TestApp />)
    await userEvent.click(screen.getByRole('button', { name: 'EN' }))
    await waitFor(() => expect(screen.getByRole('heading', { level: 1 }).textContent).toBe(en['home.title']))
    expect(document.documentElement.getAttribute('lang')).toBe('en')
    expect(localStorage.getItem('fz_lang')).toBe('en')
    expect(screen.getByRole('navigation', { name: en['nav.menu'] })).toBeTruthy()
    expect(screen.queryByText(uz['home.title'])).toBeNull()
  })

  it('switches the theme', async () => {
    serverUp(true)
    render(<TestApp />)
    await userEvent.click(screen.getByRole('button', { name: uz['theme.light'] }))
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')
    expect(useThemeStore.getState().preference).toBe('light')
    expect(screen.getByRole('button', { name: uz['theme.light'] }).getAttribute('aria-pressed')).toBe('true')
  })

  it('shows the not-found page for an unknown route', () => {
    serverUp(true)
    render(<TestApp path="/nope" />)
    expect(screen.getByText(uz['errors.not_found'])).toBeTruthy()
    // One link in the top navigation and one on the page itself.
    expect(screen.getAllByRole('link', { name: uz['nav.home'] })).toHaveLength(2)
  })

  it('keeps the Uzbek modifier letters intact in the DOM', async () => {
    serverUp(true)
    render(<TestApp />)
    expect(screen.getByText(uz['home.letters_check']).textContent).toContain('oʻqish')
    expect(i18n.language).toBe('uz')
  })
})
