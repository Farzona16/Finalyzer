import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'

import { get, patch } from '@/api/client'
import { Glossed } from '@/components/Glossed'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Tabs } from '@/components/ui/tabs'

interface CardOut {
  finding_id: string
  headline: string
  meaning: string
  next_step: string
  details: string
  status: string
}

export function InsightsPage() {
  const { t } = useTranslation()
  const { id = '' } = useParams()
  const [level, setLevel] = useState<'simple' | 'simpler' | 'expert'>('simple')
  const query = useQuery({
    queryKey: ['insights', id, level],
    queryFn: ({ signal }) => get<{ cards: CardOut[] }>(`/datasets/${id}/insights?level=${level}`, signal),
  })
  return (
    <div className="space-y-3">
      <Tabs
        value={level}
        onChange={(next) => setLevel(next as typeof level)}
        items={[
          { id: 'simple', label: t('level.simple') },
          { id: 'expert', label: t('level.expert') },
          { id: 'simpler', label: t('insights.simpler') },
        ]}
      />
      {(query.data?.cards ?? []).map((card) => (
        <Card key={card.finding_id} className="space-y-2">
          <h3 className="font-semibold">
            <Glossed text={card.headline} />
          </h3>
          <p>
            <Glossed text={card.meaning} />
          </p>
          <p className="text-sm">
            <Glossed text={card.next_step} />
          </p>
          {level === 'expert' && card.details ? <pre className="text-xs">{card.details}</pre> : null}
          <p className="text-xs text-muted">
            {card.status === 'open' ? t('insights.status_open') : t('insights.mark_done')}
          </p>
          {card.status === 'open' && (
            <Button
              variant="outline"
              size="sm"
              onClick={() =>
                void patch(`/datasets/${id}/insights/${card.finding_id}`, { status: 'done' }).then(() =>
                  query.refetch(),
                )
              }
            >
              {t('insights.mark_done')}
            </Button>
          )}
        </Card>
      ))}
    </div>
  )
}
