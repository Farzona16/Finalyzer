import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'

import { get } from '@/api/client'
import { Card, CardTitle } from '@/components/ui/card'

interface Version {
  version: string
  first_run_required: boolean
}

export function HomePage() {
  const { t } = useTranslation()
  const status = useQuery({ queryKey: ['version'], queryFn: ({ signal }) => get<Version>('/version', signal), retry: false })
  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-3xl font-semibold">{t('home.title')}</h1>
        <p className="mt-1 text-muted">{t('home.subtitle')}</p>
      </div>
      <Card className="max-w-md space-y-2">
        <CardTitle>{t('app.name')}</CardTitle>
        <p aria-live="polite">
          {status.isPending && t('common.loading')}
          {status.isError && t('home.status.down')}
          {status.data && t('home.status.ok', { version: status.data.version })}
        </p>
        <p className="text-sm text-muted">{t('home.letters_check')}</p>
      </Card>
    </div>
  )
}
