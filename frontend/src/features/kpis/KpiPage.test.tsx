import { render, screen, waitFor } from '@testing-library/react'
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
  dataset: { id: 'd1', name: 'Territory', domain: 'retail', status: 'ready' },
  dropped_pii: [],
  warnings: [],
  fact_table: 'sales',
  tables: { sales: [] },
  relationships: [],
  roles: {},
}

describe('KPI manager page', () => {
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

  it('renders metric cards, values and a percent gauge', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        const path = String(url)
        if (path.includes('/auth/csrf')) return json({ csrf_token: 'tok' })
        if (path.includes('/auth/me')) return json({ code: 'x', title: 'x', detail: 'x', status: 401 }, 401)
        if (path.includes('/profile')) return json(PROFILE)
        if (path.includes('/kpis')) {
          return json({
            domain: {
              pack: 'retail',
              label: 'Chakana',
              headline: 'Biz ma\'lumotingizni «Chakana» turiga oʻxshatdik',
              reasons: [],
            },
            tiles: ['revenue', 'margin'],
            items: [
              {
                id: 'revenue',
                label: 'Daromad',
                description: 'Jami',
                unit: 'currency',
                enabled: true,
                is_tile: true,
                value: 12_000_000,
                display: '12 mln',
                delta_display: '+10%',
                tone: 'good',
                expert: null,
              },
              {
                id: 'margin',
                label: 'Marja',
                description: 'Foiz',
                unit: 'percent',
                enabled: true,
                is_tile: true,
                value: 24,
                display: '24%',
                delta_display: '',
                tone: 'neutral',
                expert: null,
              },
            ],
            period: { start: '2026-08-01', end: '2026-09-30' },
          })
        }
        return json({ version: '3.0.0', first_run_required: true })
      }),
    )
    render(<TestApp path="/datasets/d1/kpis" />)
    await waitFor(() => expect(screen.getByText('Daromad')).toBeTruthy())
    expect(screen.getByText('12 mln')).toBeTruthy()
    expect(screen.getByText('24%')).toBeTruthy()
    expect(screen.getByRole('img', { name: 'Marja' })).toBeTruthy()
    expect(screen.getByText(uz['kpis.tiles'])).toBeTruthy()
  })

  it('explains how to get metrics when the list is empty', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        const path = String(url)
        if (path.includes('/auth/csrf')) return json({ csrf_token: 'tok' })
        if (path.includes('/auth/me')) return json({ code: 'x', title: 'x', detail: 'x', status: 401 }, 401)
        if (path.includes('/profile')) return json(PROFILE)
        if (path.includes('/kpis')) return json({ domain: null, tiles: [], items: [], period: null })
        return json({ version: '3.0.0', first_run_required: true })
      }),
    )
    render(<TestApp path="/datasets/d1/kpis" />)
    await waitFor(() => expect(screen.getByText(uz['kpis.empty'])).toBeTruthy())
    expect(screen.getByRole('button', { name: uz['dataset.build'] })).toBeTruthy()
  })
})
