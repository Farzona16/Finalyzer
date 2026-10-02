import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { resetApiState } from '@/api/client'
import { TestApp } from '@/app/App'
import { setLanguage } from '@/i18n'
import uz from '@/i18n/uz.json'

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const PROFILE = {
  dataset: { id: 'd1', name: 'Demo', domain: 'retail', status: 'ready' },
  dropped_pii: [],
  warnings: [],
  fact_table: 'sales',
  tables: { sales: [] },
  relationships: [],
  roles: {},
}

describe('insights and ask pages', () => {
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

  it('loads a simpler wording when Explain simpler is chosen', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        const path = String(url)
        if (path.includes('/auth/csrf')) return json({ csrf_token: 'tok' })
        if (path.includes('/auth/me')) return json({ code: 'x', title: 'x', detail: 'x', status: 401 }, 401)
        if (path.includes('/profile')) return json(PROFILE)
        if (path.includes('level=simpler')) {
          return json({
            cards: [
              { finding_id: 'f1', headline: 'Sodda', meaning: 'Oddiy matn', next_step: 'n', details: '', status: 'open' },
            ],
          })
        }
        if (path.includes('/insights')) {
          return json({
            cards: [
              { finding_id: 'f1', headline: 'Murakkab', meaning: 'Uzoq matn', next_step: 'n', details: '', status: 'open' },
            ],
          })
        }
        return json({ version: '3.0.0', first_run_required: true })
      }),
    )
    render(<TestApp path="/datasets/d1/insights" />)
    await waitFor(() => expect(screen.getByText('Murakkab')).toBeTruthy())
    await userEvent.click(screen.getByRole('tab', { name: uz['insights.simpler'] }))
    await waitFor(() => expect(screen.getByText('Sodda')).toBeTruthy())
    expect(screen.queryByText('Murakkab')).toBeNull()
  })

  it('asks a suggested question and shows the verified badge', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        const path = String(url)
        if (path.includes('/auth/csrf')) return json({ csrf_token: 'tok' })
        if (path.includes('/auth/me')) return json({ code: 'x', title: 'x', detail: 'x', status: 401 }, 401)
        if (path.includes('/profile')) return json(PROFILE)
        if (path.includes('/suggested-questions')) return json({ questions: ['Jami daromad qancha?'] })
        if (path.includes('/ask') && init?.method === 'POST') {
          return json({
            status: 'ok',
            headline: '12 mln',
            explanation: 'Hisoblangan qiymat: 12 mln soʻm.',
            so_what: 'Bu hozirgi holat.',
            next_step: 'Shu koʻrsatkichni oylar boʻyicha ham koʻring.',
            details: 'Savol «total» deb tushunildi. SQL kod tomonidan tuzildi.',
            sql: 'SELECT SUM(amount) FROM data',
            verified: true,
            followups: [],
            options: [{ label: 'Doʻkon boʻyicha', append: 'doʻkon' }],
            table: { columns: ['revenue'], rows: [[12_000_000]], row_count: 1 },
          })
        }
        return json({ version: '3.0.0', first_run_required: true })
      }),
    )
    render(<TestApp path="/datasets/d1/ask" />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Jami daromad qancha?' })).toBeTruthy())
    await userEvent.click(screen.getByRole('button', { name: 'Jami daromad qancha?' }))
    await waitFor(() => expect(screen.getByText(uz['ask.verified'])).toBeTruthy())
    expect(screen.getByText('12 mln')).toBeTruthy()
    expect(screen.getByText('Hisoblangan qiymat: 12 mln soʻm.')).toBeTruthy()
    expect(screen.getByText('12000000')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Doʻkon boʻyicha' })).toBeTruthy()
    HTMLDialogElement.prototype.showModal = function showModal() {
      this.setAttribute('open', '')
    }
    HTMLDialogElement.prototype.close = function closeDialog() {
      this.removeAttribute('open')
    }
    await userEvent.click(screen.getByRole('button', { name: uz['ask.how'] }))
    expect(screen.getByText(uz['ask.reasoning'])).toBeTruthy()
    expect(screen.getByText(/SQL kod tomonidan tuzildi/)).toBeTruthy()
    expect(screen.getByText(uz['ask.sql'])).toBeTruthy()
    expect(screen.getByText('SELECT SUM(amount) FROM data')).toBeTruthy()
  })
})
