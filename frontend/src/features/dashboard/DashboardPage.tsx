import { Download, FilterX, LayoutGrid, Table2, TrendingDown, TrendingUp } from 'lucide-react'
import { lazy, Suspense, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'

import { get, post } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { EmptyWithAction } from '@/components/ui/empty'
import { Skeleton } from '@/components/ui/skeleton'
import { formatCompact } from '@/lib/format'
import { currentLocale } from '@/i18n'

const Chart = lazy(() => import('./Chart'))

interface Dash {
  tiles: {
    id: string
    type: string
    title: string
    w: number
    h: number
    x: number
    y: number
    cross_filter?: boolean
    dimension?: string | null
  }[]
  slicers: { column: string; values: string[] }[]
  period: { start: string; end: string } | null
}

interface TileData {
  tile_id: string
  type: string
  categories: string[]
  series: { name: string; values: (number | null)[]; formatted: string[] }[]
  columns: string[]
  rows: unknown[][]
  formatted_rows: string[][]
}

interface Filters {
  extra_column: string | null
  extra_value: string | null
}

function deltaClass(text: string): string {
  if (!text || /n\/a|—|–/i.test(text)) return 'text-muted'
  if (text.includes('-') || text.includes('−')) return 'text-red'
  if (text.includes('+')) return 'text-green'
  return 'text-muted'
}

function TileCard({
  datasetId,
  tile,
  filters,
  onSelect,
  data,
  pending,
  failed,
  onRetry,
}: {
  datasetId: string
  tile: Dash['tiles'][0]
  filters: Filters
  onSelect: (column: string, value: string) => void
  data: TileData | undefined
  pending: boolean
  failed: boolean
  onRetry: () => void
}) {
  const { t } = useTranslation()
  const [asTable, setAsTable] = useState(false)
  void datasetId
  void filters
  return (
    <Card
      className="space-y-2"
      style={{ gridColumn: `span ${Math.min(tile.w, 12)} / span ${Math.min(tile.w, 12)}` }}
    >
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">{tile.title}</h3>
        <div className="flex gap-1">
          <Button variant="ghost" size="sm" onClick={() => setAsTable((v) => !v)}>
            {asTable ? <LayoutGrid aria-hidden="true" className="h-4 w-4" /> : <Table2 aria-hidden="true" className="h-4 w-4" />}
            {asTable ? t('view.chart') : t('view.table')}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              const lines = (data?.formatted_rows ?? []).map((r) => r.join(','))
              const blob = new Blob([lines.join('\n')], { type: 'text/csv' })
              const url = URL.createObjectURL(blob)
              const a = document.createElement('a')
              a.href = url
              a.download = `${tile.id}.csv`
              a.click()
              URL.revokeObjectURL(url)
            }}
          >
            <Download aria-hidden="true" className="h-4 w-4" />
            {t('export.csv')}
          </Button>
        </div>
      </div>
      {pending && <Skeleton className="h-40" />}
      {failed && (
        <EmptyWithAction title={t('errors.generic')}>
          <Button variant="ghost" size="sm" onClick={onRetry}>
            {t('common.retry')}
          </Button>
        </EmptyWithAction>
      )}
      {data && asTable && (
        <div className="overflow-auto text-sm">
          <table className="w-full">
            <tbody>
              {(data.formatted_rows.length ? data.formatted_rows : data.rows).slice(0, 20).map((row, i) => (
                <tr key={i}>
                  {(row as unknown[]).map((cell, j) => (
                    <td key={j} className="border-b border-border/40 px-2 py-1">
                      {String(cell ?? '')}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {data && !asTable && (
        <Suspense fallback={<Skeleton className="h-40" />}>
          <Chart
            tile={data}
            onSelect={
              tile.cross_filter && tile.dimension
                ? (name) => onSelect(tile.dimension as string, name)
                : undefined
            }
          />
        </Suspense>
      )}
    </Card>
  )
}

export function DashboardPage() {
  const { t } = useTranslation()
  const { id = '' } = useParams()
  const [filters, setFilters] = useState<Filters>({ extra_column: null, extra_value: null })
  const dash = useQuery({
    queryKey: ['dashboard', id],
    queryFn: ({ signal }) => get<Dash>(`/datasets/${id}/dashboard`, signal),
  })
  const kpis = useQuery({
    queryKey: ['kpis', id],
    queryFn: ({ signal }) =>
      get<{ items: { id: string; label: string; display: string; delta_display: string; is_tile: boolean }[] }>(
        `/datasets/${id}/kpis`,
        signal,
      ),
  })
  const tiles = dash.data?.tiles ?? []
  const chartTiles = tiles.filter((tile) => tile.type !== 'kpi_row')
  const batch = useQuery({
    queryKey: ['tiles-batch', id, filters, chartTiles.map((tile) => tile.id)],
    enabled: Boolean(id) && chartTiles.length > 0,
    queryFn: () =>
      post<TileData[]>(`/datasets/${id}/tiles/query-batch`, {
        tile_ids: chartTiles.map((tile) => tile.id),
        filters,
      }),
  })
  const byId = useMemo(() => {
    const map = new Map<string, TileData>()
    for (const row of batch.data ?? []) map.set(row.tile_id, row)
    return map
  }, [batch.data])
  const kpiTiles = kpis.data?.items.filter((i) => i.is_tile) ?? []
  const locale = currentLocale()
  const period = dash.data?.period
  const slicers = dash.data?.slicers ?? []
  const chip = useMemo(
    () => (filters.extra_column && filters.extra_value ? `${filters.extra_column}: ${filters.extra_value}` : ''),
    [filters],
  )
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted">{period ? `${period.start} – ${period.end}` : t('common.loading')}</p>
      {slicers.length > 0 && (
        <p className="sr-only">{slicers.map((slicer) => slicer.column).join(' ')}</p>
      )}
      {chip && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm">{t('filters.active')}</span>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => setFilters({ extra_column: null, extra_value: null })}
          >
            {chip}
          </Button>
          <Button variant="ghost" size="sm" onClick={() => setFilters({ extra_column: null, extra_value: null })}>
            <FilterX aria-hidden="true" className="h-4 w-4" />
            {t('filters.clear')}
          </Button>
        </div>
      )}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {kpiTiles.map((kpi) => {
          const tone = deltaClass(kpi.delta_display)
          const down = tone === 'text-red'
          const up = tone === 'text-green'
          return (
            <Card key={kpi.id} className="space-y-1">
              <p className="text-xs uppercase tracking-wide text-muted">{kpi.label}</p>
              <p className="text-2xl font-semibold text-green">{kpi.display || formatCompact(0, locale)}</p>
              {kpi.delta_display ? (
                <p className={`flex items-center gap-1 text-sm ${tone}`}>
                  {down ? <TrendingDown aria-hidden="true" className="h-3.5 w-3.5" /> : null}
                  {up ? <TrendingUp aria-hidden="true" className="h-3.5 w-3.5" /> : null}
                  {kpi.delta_display}
                </p>
              ) : null}
            </Card>
          )
        })}
      </div>
      <div className="grid grid-cols-12 gap-3">
        {chartTiles.map((tile) => (
            <div key={tile.id} className="col-span-12 md:col-span-6">
              <TileCard
                datasetId={id}
                tile={tile}
                filters={filters}
                data={byId.get(tile.id)}
                pending={batch.isPending}
                failed={batch.isError}
                onRetry={() => void batch.refetch()}
                onSelect={(column, value) =>
                  setFilters((prev) =>
                    prev.extra_column === column && prev.extra_value === value
                      ? { extra_column: null, extra_value: null }
                      : { extra_column: column, extra_value: value },
                  )
                }
              />
            </div>
          ))}
      </div>
    </div>
  )
}
