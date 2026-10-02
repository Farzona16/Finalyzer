import { Activity, Eye, EyeOff, Gauge as GaugeIcon, Minus, TrendingDown, TrendingUp } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { useNavigate, useParams } from 'react-router-dom'

import { errorMessage, get, patch } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { EmptyWithAction } from '@/components/ui/empty'
import { Gauge } from '@/components/ui/gauge'
import { Skeleton } from '@/components/ui/skeleton'
import { useMeQuery } from '@/session/store'

interface Kpi {
  id: string
  label: string
  description: string
  unit: string
  enabled: boolean
  is_tile: boolean
  value: number | null
  display: string
  delta_display: string
  tone: string
  expert: { definition?: string; sql?: string } | null
}

interface KpiList {
  domain: { pack: string; label: string; headline: string; reasons: string[] } | null
  items: Kpi[]
  period: { start: string; end: string } | null
}

function toneClass(tone: string): string {
  if (tone === 'good') return 'text-green'
  if (tone === 'bad') return 'text-red'
  return 'text-muted'
}

function Delta({ tone, text }: { tone: string; text: string }) {
  if (!text) return null
  const Icon = tone === 'bad' ? TrendingDown : tone === 'good' ? TrendingUp : Minus
  return (
    <p className={`flex items-center gap-1 text-sm ${toneClass(tone)}`}>
      <Icon aria-hidden="true" className="h-3.5 w-3.5" />
      {text}
    </p>
  )
}

function MetricCard({
  item,
  canEdit,
  onToggle,
}: {
  item: Kpi
  canEdit: boolean
  onToggle: () => void
}) {
  const { t } = useTranslation()
  const percent = item.unit === 'percent'
  return (
    <Card className="flex flex-col gap-3">
      <div className="flex items-start justify-between gap-2">
        <p className="text-xs uppercase tracking-wide text-muted">{item.label}</p>
        {canEdit && (
          <Button variant="outline" size="sm" onClick={onToggle}>
            {item.enabled ? <Eye aria-hidden="true" className="h-4 w-4" /> : <EyeOff aria-hidden="true" className="h-4 w-4" />}
            {item.enabled ? t('kpis.enabled') : t('kpis.disabled')}
          </Button>
        )}
      </div>
      {percent ? (
        <div className="flex flex-col items-center">
          <Gauge value={item.value} tone={item.tone} label={item.label} />
          <p className="text-2xl font-semibold text-green">{item.display}</p>
        </div>
      ) : (
        <p className="text-3xl font-semibold text-green">{item.display}</p>
      )}
      <Delta tone={item.tone} text={item.delta_display} />
      {item.description ? <p className="text-sm text-muted">{item.description}</p> : null}
    </Card>
  )
}

export function KpiPage() {
  const { t } = useTranslation()
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const me = useMeQuery()
  const canEdit = me.data?.permissions.includes('edit_kpis') ?? false
  const query = useQuery({
    queryKey: ['kpis', id],
    queryFn: ({ signal }) => get<KpiList>(`/datasets/${id}/kpis`, signal),
  })

  if (query.isPending) {
    return (
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <Skeleton className="h-40" />
        <Skeleton className="h-40" />
        <Skeleton className="h-40" />
      </div>
    )
  }

  if (query.isError) {
    return (
      <EmptyWithAction title={errorMessage(query.error)}>
        <Button variant="ghost" size="sm" onClick={() => void query.refetch()}>
          {t('common.retry')}
        </Button>
      </EmptyWithAction>
    )
  }

  const items = query.data?.items ?? []
  const tiles = items.filter((item) => item.is_tile)
  const rest = items.filter((item) => !item.is_tile)

  if (items.length === 0) {
    return (
      <EmptyWithAction title={t('common.empty')} hint={t('kpis.empty')}>
        <Button onClick={() => navigate(`/datasets/${id}`)}>{t('dataset.build')}</Button>
      </EmptyWithAction>
    )
  }

  function toggle(item: Kpi) {
    void patch(`/datasets/${id}/kpis/${item.id}`, { enabled: !item.enabled }).then(() => query.refetch())
  }

  return (
    <div className="space-y-5">
      {query.data?.domain && (
        <Card className="space-y-1">
          <p className="flex items-center gap-2 text-sm font-medium">
            <Activity aria-hidden="true" className="h-4 w-4 text-blue" />
            {query.data.domain.headline}
          </p>
          {query.data.domain.reasons.map((reason) => (
            <p key={reason} className="text-sm text-muted">
              {reason}
            </p>
          ))}
        </Card>
      )}
      {query.data?.period ? (
        <p className="text-sm text-muted">
          {query.data.period.start} – {query.data.period.end}
        </p>
      ) : null}
      <section className="space-y-3">
        <h2 className="flex items-center gap-2 text-lg font-semibold">
          <GaugeIcon aria-hidden="true" className="h-5 w-5 text-teal" />
          {t('kpis.tiles')}
        </h2>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {tiles.map((item) => (
            <MetricCard key={item.id} item={item} canEdit={canEdit} onToggle={() => toggle(item)} />
          ))}
        </div>
      </section>
      {rest.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-lg font-semibold">{t('kpis.all')}</h2>
          <div className="grid gap-3 sm:grid-cols-2">
            {rest.map((item) => (
              <MetricCard key={item.id} item={item} canEdit={canEdit} onToggle={() => toggle(item)} />
            ))}
          </div>
        </section>
      )}
    </div>
  )
}
