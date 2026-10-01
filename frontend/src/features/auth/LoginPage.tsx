import { useQuery } from '@tanstack/react-query'
import { type FormEvent, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, Navigate, useNavigate } from 'react-router-dom'

import { errorMessage, post } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Card, CardTitle } from '@/components/ui/card'
import { useToasts } from '@/components/ui/toast'
import { type Me, useMeQuery, useSession } from '@/session/store'

export function LoginPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const setMe = useSession((s) => s.setMe)
  const me = useMeQuery()
  const push = useToasts((s) => s.push)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const version = useQuery({
    queryKey: ['version'],
    queryFn: ({ signal }) =>
      fetch('/api/v1/version', { signal }).then((r) => r.json() as Promise<{ first_run_required: boolean }>),
  })
  if (me.data) return <Navigate to="/" replace />
  if (version.data?.first_run_required) return <Navigate to="/setup" replace />

  async function onSubmit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    try {
      const signed = await post<Me>('/auth/login', { email, password })
      setMe(signed)
      navigate('/')
    } catch (error) {
      push(errorMessage(error), 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card className="mx-auto mt-16 max-w-md space-y-4">
      <CardTitle>{t('auth.login_title')}</CardTitle>
      <form className="space-y-3" onSubmit={(e) => void onSubmit(e)}>
        <label className="flex flex-col gap-1 text-sm">
          {t('auth.email')}
          <input
            className="h-10 rounded-lg border border-border bg-bg px-3"
            type="email"
            autoComplete="username"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          {t('auth.password')}
          <input
            className="h-10 rounded-lg border border-border bg-bg px-3"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>
        <Button type="submit" disabled={busy}>
          {t('auth.login')}
        </Button>
      </form>
      <p className="text-sm text-muted">
        <Link to="/setup" className="text-blue underline">
          {t('auth.first_admin')}
        </Link>
      </p>
    </Card>
  )
}
