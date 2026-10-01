import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { LoginPage } from '@/features/auth/LoginPage'
import { useMeQuery } from '@/session/store'

export function RequireAuth({ children }: { children: React.ReactNode }) {
  const { t } = useTranslation()
  const me = useMeQuery()
  const version = useQuery({
    queryKey: ['version'],
    queryFn: ({ signal }) =>
      fetch('/api/v1/version', { signal: signal ?? null }).then(
        (r) => r.json() as Promise<{ first_run_required: boolean }>,
      ),
    retry: false,
  })
  if (me.isPending || version.isPending) return <p>{t('common.loading')}</p>
  if (me.data) return <>{children}</>
  if (version.data?.first_run_required) return <Link to="/setup">{t('auth.first_admin')}</Link>
  return <LoginPage />
}
