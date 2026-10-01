import { type FormEvent, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Navigate, useNavigate } from 'react-router-dom'

import { errorMessage, post } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Card, CardTitle } from '@/components/ui/card'
import { useToasts } from '@/components/ui/toast'
import { type Me, useMeQuery, useSession } from '@/session/store'

export function SetupPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const setMe = useSession((s) => s.setMe)
  const me = useMeQuery()
  const push = useToasts((s) => s.push)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [workspace, setWorkspace] = useState('')
  const [busy, setBusy] = useState(false)
  if (me.data) return <Navigate to="/" replace />

  async function onSubmit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    try {
      const signed = await post<Me>('/auth/first-admin', {
        email,
        password,
        display_name: displayName,
        workspace_name: workspace,
      })
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
      <CardTitle>{t('auth.setup_title')}</CardTitle>
      <p className="text-sm text-muted">{t('auth.setup_hint')}</p>
      <form className="space-y-3" onSubmit={(e) => void onSubmit(e)}>
        <Field label={t('auth.workspace')} value={workspace} onChange={setWorkspace} />
        <Field label={t('auth.display_name')} value={displayName} onChange={setDisplayName} />
        <Field label={t('auth.email')} value={email} onChange={setEmail} type="email" />
        <Field label={t('auth.password')} value={password} onChange={setPassword} type="password" />
        <Button type="submit" disabled={busy}>
          {t('auth.create')}
        </Button>
      </form>
    </Card>
  )
}

function Field({
  label,
  value,
  onChange,
  type = 'text',
}: {
  label: string
  value: string
  onChange: (v: string) => void
  type?: string
}) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      {label}
      <input
        className="h-10 rounded-lg border border-border bg-bg px-3"
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        required
      />
    </label>
  )
}
