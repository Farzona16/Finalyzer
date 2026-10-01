import { type FormEvent, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'

import { errorMessage, get, post } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Card, CardTitle } from '@/components/ui/card'
import { Select } from '@/components/ui/select'
import { useToasts } from '@/components/ui/toast'

interface UserRow {
  id: string
  email: string
  display_name: string
  role: string
}

export function AdminPage() {
  const { t } = useTranslation()
  const push = useToasts((s) => s.push)
  const client = useQueryClient()
  const users = useQuery({
    queryKey: ['admin-users'],
    queryFn: ({ signal }) => get<UserRow[]>('/admin/users', signal),
  })
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [name, setName] = useState('')
  const [role, setRole] = useState('viewer')

  async function onSubmit(event: FormEvent) {
    event.preventDefault()
    try {
      await post('/admin/users', { email, password, display_name: name, role })
      setEmail('')
      setPassword('')
      setName('')
      await client.invalidateQueries({ queryKey: ['admin-users'] })
    } catch (error) {
      push(errorMessage(error), 'error')
    }
  }

  return (
    <div className="space-y-5">
      <Card className="space-y-3">
        <CardTitle>{t('admin.title')}</CardTitle>
        {(users.data ?? []).map((u) => (
          <p key={u.id} className="text-sm">
            {u.display_name} ({u.email}) — {u.role}
          </p>
        ))}
      </Card>
      <Card className="space-y-3">
        <CardTitle>{t('admin.create')}</CardTitle>
        <form className="space-y-2" onSubmit={(e) => void onSubmit(e)}>
          <input className="h-10 w-full rounded-lg border border-border px-3" value={name} onChange={(e) => setName(e.target.value)} placeholder={t('auth.display_name')} required />
          <input className="h-10 w-full rounded-lg border border-border px-3" type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder={t('auth.email')} required />
          <input className="h-10 w-full rounded-lg border border-border px-3" type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder={t('auth.password')} required />
          <Select
            label={t('admin.role')}
            value={role}
            onChange={setRole}
            options={[
              { value: 'viewer', label: 'viewer' },
              { value: 'analyst', label: 'analyst' },
              { value: 'admin', label: 'admin' },
            ]}
          />
          <Button type="submit">{t('admin.create')}</Button>
        </form>
      </Card>
    </div>
  )
}
