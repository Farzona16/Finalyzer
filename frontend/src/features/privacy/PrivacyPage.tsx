import { Shield } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'

import { get } from '@/api/client'
import { Badge } from '@/components/ui/badge'
import { Card, CardTitle } from '@/components/ui/card'

interface EventRow {
  purpose: string
  payload_bytes: number
  blocked: boolean
  payload_text: string
}

export function PrivacyPage() {
  const { t } = useTranslation()
  const summary = useQuery({
    queryKey: ['privacy'],
    queryFn: ({ signal }) => get<{ bytes_sent: number; mode: string }>(`/privacy/summary`, signal),
  })
  const events = useQuery({
    queryKey: ['privacy-events'],
    queryFn: ({ signal }) => get<{ events: EventRow[] }>(`/privacy/events`, signal),
  })
  return (
    <Card className="space-y-2">
      <CardTitle className="flex items-center gap-2">
        <Shield aria-hidden="true" className="h-4 w-4 text-teal" />
        {t('privacy.title')}
      </CardTitle>
      <p>{t('privacy.bytes', { n: summary.data?.bytes_sent ?? 0 })}</p>
      <p className="text-sm text-muted">{summary.data?.mode}</p>
      {(events.data?.events ?? []).map((row, i) => (
        <pre key={i} className="overflow-auto rounded-lg bg-surface-2/80 p-2 text-xs">
          {row.purpose} ({row.payload_bytes})
          {row.blocked ? <Badge tone="red" className="ml-2 align-middle">blocked</Badge> : null}: {row.payload_text}
        </pre>
      ))}
    </Card>
  )
}
