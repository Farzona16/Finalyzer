import {
  BarChart3,
  Columns3,
  Gauge,
  GitMerge,
  Lightbulb,
  Link2,
  MessageSquare,
  Settings2,
  ShieldAlert,
  Table2,
  WandSparkles,
} from 'lucide-react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { NavLink, Outlet, useNavigate, useParams } from 'react-router-dom'

import { errorMessage, get, patch, post } from '@/api/client'
import { waitJob } from '@/api/jobs'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Select } from '@/components/ui/select'
import { useToasts } from '@/components/ui/toast'
import { cn } from '@/lib/utils'
import { useMeQuery } from '@/session/store'

const DOMAINS = ['retail', 'bank', 'sales', 'statistics', 'generic', 'project', 'expenses'] as const

interface Join {
  fact_col: string
  dim_table: string
  dim_col: string
  containment?: number
}

interface Column {
  name: string
}

interface Profile {
  dataset: { id: string; name: string; domain: string | null; status: string; tables?: Record<string, number> }
  dropped_pii: string[]
  warnings: string[]
  fact_table: string
  tables: Record<string, Column[]>
  columns?: Column[]
  relationships: Join[]
  proposed_relationships?: Join[]
  roles: Record<string, string>
}

function joinKey(item: Join) {
  return `${item.fact_col}|${item.dim_table}|${item.dim_col}`
}

const TAB_ICONS = [Table2, BarChart3, Lightbulb, MessageSquare, Gauge] as const

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
      <h1 className="text-2xl font-semibold tracking-tight">{profile.data?.dataset.name ?? t('common.loading')}</h1>
      <nav className="flex flex-wrap gap-2">
        {tabs.map((tab, index) => {
          const Icon = TAB_ICONS[index]
          return (
            <NavLink
              key={tab.to}
              to={tab.to}
              end={tab.end}
              className={({ isActive }) =>
                cn(
                  'interactive inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm',
                  'transition-all duration-200 ease-in-out',
                  isActive ? 'bg-surface-2 text-text shadow-glow' : 'text-muted hover:bg-surface-2/60 hover:text-text',
                )
              }
            >
              {Icon ? <Icon aria-hidden="true" className="h-4 w-4" /> : null}
              {tab.label}
            </NavLink>
          )
        })}
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

  async function saveFact(next: string) {
    try {
      await patch(`/datasets/${id}/model`, { fact_table: next })
      await client.invalidateQueries({ queryKey: ['profile', id] })
    } catch (error) {
      push(errorMessage(error), 'error')
    }
  }

  async function saveJoins(next: Join[]) {
    try {
      await patch(`/datasets/${id}/model`, {
        relationships: next.map((item) => ({
          fact_col: item.fact_col,
          dim_table: item.dim_table,
          dim_col: item.dim_col,
        })),
      })
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
  const counts = row.dataset.tables ?? {}
  const tableNames = Object.keys(row.tables)
  const active = new Set(row.relationships.map(joinKey))
  const listed: Join[] = [...(row.proposed_relationships ?? [])]
  for (const item of row.relationships) {
    if (!listed.some((other) => joinKey(other) === joinKey(item))) listed.push(item)
  }
  const flat = (row.columns ?? []).map((c) => c.name)

  return (
    <div className="space-y-3">
      <Card className="space-y-3">
        <p className="flex items-center gap-2">
          <GitMerge aria-hidden="true" className="h-4 w-4 text-blue" />
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
        <div>
          <h2 className="flex items-center gap-2 font-semibold">
            <Table2 aria-hidden="true" className="h-4 w-4 text-teal" />
            {t('dataset.tables_found')}
          </h2>
          <ul className="mt-1 text-sm text-muted">
            {tableNames.map((name) => (
              <li key={name}>
                {name}
                {counts[name] != null ? ` · ${t('dataset.n_rows', { n: counts[name] })}` : ''}
              </li>
            ))}
          </ul>
        </div>
        <div>
          <h2 className="flex items-center gap-2 font-semibold">
            <Link2 aria-hidden="true" className="h-4 w-4 text-blue" />
            {t('dataset.joins')}
          </h2>
          {row.relationships.length === 0 ? (
            <p className="text-sm text-muted">{t('dataset.no_joins')}</p>
          ) : (
            <ul className="mt-1 text-sm">
              {row.relationships.map((item) => (
                <li key={joinKey(item)}>
                  {t('dataset.join_on', { fact_col: item.fact_col, dim_table: item.dim_table, dim_col: item.dim_col })}
                </li>
              ))}
            </ul>
          )}
        </div>
        {flat.length > 0 && (
          <div>
            <h2 className="flex items-center gap-2 font-semibold">
              <Columns3 aria-hidden="true" className="h-4 w-4 text-green" />
              {t('dataset.flat_columns')}
            </h2>
            <p className="text-sm text-muted">{flat.join(', ')}</p>
          </div>
        )}
        <div>
          <h2 className="flex items-center gap-2 font-semibold">
            <ShieldAlert aria-hidden="true" className="h-4 w-4 text-amber" />
            {t('dataset.pii')}
          </h2>
          <p className="text-sm text-muted">{row.dropped_pii.join(', ') || t('common.empty')}</p>
        </div>
      </Card>
      <Button variant="ghost" size="sm" onClick={() => setAdvanced((v) => !v)}>
        <Settings2 aria-hidden="true" className="h-4 w-4" />
        {t('dataset.advanced')}
      </Button>
      {advanced && (
        <Card className="space-y-3">
          {canBuild && tableNames.length > 0 && (
            <Select
              label={t('dataset.fact')}
              value={row.fact_table}
              onChange={(value) => void saveFact(value)}
              options={tableNames.map((name) => ({ value: name, label: name }))}
            />
          )}
          {listed.length === 0 ? (
            <p className="text-sm text-muted">{t('dataset.no_joins')}</p>
          ) : (
            listed.map((item) => (
              <label key={joinKey(item)} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={active.has(joinKey(item))}
                  disabled={!canBuild}
                  onChange={(event) => {
                    const next = event.target.checked
                      ? [...row.relationships, item]
                      : row.relationships.filter((other) => joinKey(other) !== joinKey(item))
                    void saveJoins(next)
                  }}
                />
                {t('dataset.join_on', { fact_col: item.fact_col, dim_table: item.dim_table, dim_col: item.dim_col })}
              </label>
            ))
          )}
        </Card>
      )}
      {row.warnings.map((w) => (
        <p key={w} className="text-sm text-muted">
          {w}
        </p>
      ))}
      {canBuild && (
        <Button disabled={busy} onClick={() => void build()}>
          <WandSparkles aria-hidden="true" className="h-4 w-4" />
          {t('dataset.build')}
        </Button>
      )}
    </div>
  )
}
