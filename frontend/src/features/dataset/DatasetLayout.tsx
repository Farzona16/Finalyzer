import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { NavLink, Outlet, useNavigate, useParams } from 'react-router-dom'

import { errorMessage, get, patch, post } from '@/api/client'
import { waitJob } from '@/api/jobs'
import { Button } from '@/components/ui/button'
import { Select } from '@/components/ui/select'
import { useToasts } from '@/components/ui/toast'
import { cn } from '@/lib/utils'
import { useMeQuery } from '@/session/store'

const DOMAINS = ['retail', 'bank', 'sales', 'statistics', 'generic', 'project', 'expenses'] as const

interface Profile {
  dataset: { id: string; name: string; domain: string | null; status: string }
  dropped_pii: string[]
  warnings: string[]
  fact_table: string
  tables: Record<string, unknown[]>
  relationships: { fact_col: string; dim_table: string; dim_col: string }[]
  roles: Record<string, string>
}

export function DatasetLayout() {
  const { t } = useTranslation()
  const { id = '' } = useParams()
  const profile = useQuery({
    queryKey: ['profile', id],
    queryFn: ({ signal }) => get<Profile>(`/datasets/${id}/profile`, signal),
  })
  const tabs = [
    { to: `/datasets/${id}`, end: true, label: t('dataset.summary') },
    { to: `/datasets/${id}/dashboard`, label: t('dataset.dashboard') },
    { to: `/datasets/${id}/insights`, label: t('dataset.insights') },
    { to: `/datasets/${id}/ask`, label: t('dataset.ask') },
    { to: `/datasets/${id}/kpis`, label: t('dataset.kpis') },
  ]
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">{profile.data?.dataset.name ?? t('common.loading')}</h1>
      <nav className="flex flex-wrap gap-2">
        {tabs.map((tab) => (
          <NavLink
            key={tab.to}
            to={tab.to}
            end={tab.end}
            className={({ isActive }) =>
              cn('rounded-lg px-3 py-1.5 text-sm', isActive ? 'bg-surface-2' : 'text-muted')
            }
          >
            {tab.label}
          </NavLink>
        ))}
      </nav>
      <Outlet />
    </div>
  )
}

export function SummaryPage() {
  const { t } = useTranslation()
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const push = useToasts((s) => s.push)
  const client = useQueryClient()
  const me = useMeQuery()
  const canBuild = me.data?.permissions.includes('upload') ?? false
  const [busy, setBusy] = useState(false)
  const [advanced, setAdvanced] = useState(false)
  const profile = useQuery({
    queryKey: ['profile', id],
    queryFn: ({ signal }) => get<Profile>(`/datasets/${id}/profile`, signal),
  })
  const row = profile.data
  const [domain, setDomain] = useState(row?.dataset.domain ?? 'generic')

  async function saveDomain(next: string) {
    setDomain(next)
    try {
      await patch(`/datasets/${id}/model`, { domain: next })
      await client.invalidateQueries({ queryKey: ['profile', id] })
    } catch (error) {
      push(errorMessage(error), 'error')
    }
  }

  async function build() {
    setBusy(true)
    try {
      const job = await post<{ id: string }>(`/datasets/${id}/build`)
      const done = await waitJob(job.id)
      if (done.status === 'failed') throw new Error(done.error?.detail || t('jobs.failed'))
      await client.invalidateQueries({ queryKey: ['profile', id] })
      navigate(`/datasets/${id}/dashboard`)
    } catch (error) {
      push(errorMessage(error), 'error')
    } finally {
      setBusy(false)
    }
  }

  if (!row) return <p>{t('common.loading')}</p>
  const tables = Object.entries(row.tables)
  return (
    <div className="space-y-3">
      <p>
        {t('dataset.domain')}: {row.dataset.domain ?? '—'}
      </p>
      {canBuild && (
        <Select
          label={t('dataset.change_domain')}
          value={domain || row.dataset.domain || 'generic'}
          onChange={(value) => void saveDomain(value)}
          options={DOMAINS.map((name) => ({ value: name, label: name }))}
        />
      )}
      <p className="text-sm text-muted">
        {tables.map(([name, cols]) => `${name} (${cols.length})`).join(' · ') || t('common.empty')}
      </p>
      <div>
        <h2 className="font-semibold">{t('dataset.pii')}</h2>
        <p className="text-sm text-muted">{row.dropped_pii.join(', ') || t('common.empty')}</p>
      </div>
      <Button variant="ghost" size="sm" onClick={() => setAdvanced((v) => !v)}>
        {t('dataset.advanced')}
      </Button>
      {advanced && (
        <pre className="overflow-auto rounded-lg bg-surface-2 p-3 text-xs">
          {JSON.stringify({ fact: row.fact_table, roles: row.roles, joins: row.relationships }, null, 2)}
        </pre>
      )}
      {row.warnings.map((w) => (
        <p key={w} className="text-sm text-muted">
          {w}
        </p>
      ))}
      {canBuild && (
        <Button disabled={busy} onClick={() => void build()}>
          {t('dataset.build')}
        </Button>
      )}
    </div>
  )
}
