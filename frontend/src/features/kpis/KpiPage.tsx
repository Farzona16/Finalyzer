import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'

import { get, patch } from '@/api/client'
import { Button } from '@/components/ui/button'

interface Kpi {
  id: string
  label: string
  display: string
  enabled: boolean
  is_tile: boolean
}

export function KpiPage() {
  const { t } = useTranslation()
  const { id = '' } = useParams()
  const query = useQuery({
    queryKey: ['kpis', id],
    queryFn: ({ signal }) => get<{ items: Kpi[] }>(`/datasets/${id}/kpis`, signal),
  })
  return (
    <div className="space-y-2">
      {(query.data?.items ?? []).map((item) => (
        <div key={item.id} className="flex items-center justify-between gap-3 rounded-lg border border-border p-3">
          <div>
            <p className="font-medium">{item.label}</p>
            <p className="text-sm text-muted">{item.display}</p>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => void patch(`/datasets/${id}/kpis/${item.id}`, { enabled: !item.enabled }).then(() => query.refetch())}
          >
            {item.enabled ? t('kpis.enabled') : t('kpis.disabled')}
          </Button>
        </div>
      ))}
    </div>
  )
}
