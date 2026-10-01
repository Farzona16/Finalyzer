import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { resetApiState } from '@/api/client'
import { setLanguage } from '@/i18n'
import uz from '@/i18n/uz.json'
import { TestApp } from '@/app/App'

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function problem(status: number, code: string) {
  return new Response(JSON.stringify({ code, title: code, detail: code, status }), {
    status,
    headers: { 'Content-Type': 'application/problem+json' },
  })
}

describe('signed-in home and summary', () => {
  beforeEach(async () => {
    localStorage.clear()
    resetApiState()
    vi.stubGlobal('matchMedia', () => ({
      matches: false,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    }))
    await setLanguage('uz')
  })
  afterEach(() => localStorage.clear())

  it('hides upload for a viewer', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        const path = String(url)
        if (path.includes('/auth/csrf')) return json({ csrf_token: 'tok' })
        if (path.includes('/auth/me')) {
          return json({
            user: {
              id: 'u1',
              email: 'v@example.com',
              display_name: 'V',
              role: 'viewer',
              language: 'uz',
              theme: 'system',
              reading_level: 'simple',
            },
            workspace: { id: 'w1', name: 'W', privacy_mode: 'demo', currency: 'UZS', language: 'uz' },
            permissions: ['read'],
          })
        }
        if (path.includes('/version')) return json({ version: '3.0.0', first_run_required: false })
        if (path.includes('/demo')) return json([])
        if (path.includes('/datasets')) return json([])
        return problem(404, 'not_found')
      }),
    )
    render(<TestApp />)
    await waitFor(() => expect(screen.getByText(uz['home.viewer_hint'])).toBeTruthy())
    expect(screen.queryByRole('button', { name: uz['home.upload'] })).toBeNull()
  })

  it('lists removed personal columns on the summary page', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        const path = String(url)
        if (path.includes('/auth/csrf')) return json({ csrf_token: 'tok' })
        if (path.includes('/auth/me')) {
          return json({
            user: {
              id: 'u1',
              email: 'a@example.com',
              display_name: 'A',
              role: 'admin',
              language: 'uz',
              theme: 'system',
              reading_level: 'simple',
            },
            workspace: { id: 'w1', name: 'W', privacy_mode: 'demo', currency: 'UZS', language: 'uz' },
            permissions: ['read', 'upload'],
          })
        }
        if (path.includes('/profile')) {
          return json({
            dataset: { id: 'd1', name: 'Demo', domain: 'retail', status: 'profiled' },
            dropped_pii: ['email', 'phone'],
            warnings: [],
            fact_table: 'sales',
            tables: { sales: [{ name: 'amount' }] },
            relationships: [],
            roles: { amount: 'amount' },
          })
        }
        if (path.includes('/version')) return json({ version: '3.0.0', first_run_required: false })
        return problem(404, 'not_found')
      }),
    )
    render(<TestApp path="/datasets/d1" />)
    await waitFor(() => expect(screen.getByText(/email, phone/)).toBeTruthy())
    expect(screen.getByText(uz['dataset.pii'])).toBeTruthy()
    expect(screen.getByRole('button', { name: uz['dataset.build'] })).toBeTruthy()
  })
})
