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

interface Summary {
  bytes_sent: number
  mode: string
  tokens_used: number
  template_mode: boolean
  kill_switch: boolean
  budgets: Record<string, { used: number; limit: number; remaining: number }>
  providers: { name: string; model: string; state: string }[]
}

export function PrivacyPage() {
  const { t } = useTranslation()
  const summary = useQuery({
    queryKey: ['privacy'],
    queryFn: ({ signal }) => get<Summary>(`/privacy/summary`, signal),
  })
  const events = useQuery({
    queryKey: ['privacy-events'],
    queryFn: ({ signal }) => get<{ events: EventRow[] }>(`/privacy/events`, signal),
  })
  const session = summary.data?.budgets?.session
  const daily = summary.data?.budgets?.daily
  return (
    <Card className="space-y-2">
      <CardTitle className="flex items-center gap-2">
        <Shield aria-hidden="true" className="h-4 w-4 text-teal" />
        {t('privacy.title')}
      </CardTitle>
      <p className="text-sm text-muted">{t('app.ephemeral')}</p>
      <p>{t('privacy.bytes', { n: summary.data?.bytes_sent ?? 0 })}</p>
      {session ? (
        <p>{t('privacy.tokens', { used: session.used, remaining: session.remaining })}</p>
      ) : null}
      {daily ? <p className="text-sm text-muted">{t('privacy.daily', { used: daily.used, limit: daily.limit })}</p> : null}
      <p className="text-sm text-muted">{summary.data?.mode}</p>
      {summary.data?.template_mode ? <Badge tone="amber">{t('privacy.template')}</Badge> : null}
      {summary.data?.kill_switch ? <Badge tone="red">{t('privacy.kill_switch')}</Badge> : null}
      <div className="flex flex-wrap gap-2">
        {(summary.data?.providers ?? []).map((row) => (
          <Badge key={row.name} tone={row.state === 'ok' ? 'green' : 'amber'}>
            {row.name}
            {row.model ? ` · ${row.model}` : ''} · {row.state}
          </Badge>
        ))}
      </div>
      {(events.data?.events ?? []).map((row, i) => (
        <pre key={i} className="overflow-auto rounded-lg bg-surface-2/80 p-2 text-xs">
          {row.purpose} ({row.payload_bytes})
          {row.blocked ? <Badge tone="red" className="ml-2 align-middle">blocked</Badge> : null}: {row.payload_text}
        </pre>
      ))}
    </Card>
  )
}
