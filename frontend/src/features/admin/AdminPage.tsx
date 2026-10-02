import { UserPlus, Users } from 'lucide-react'
import { type FormEvent, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'

import { errorMessage, get, post } from '@/api/client'
import { Badge } from '@/components/ui/badge'
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
        <CardTitle className="flex items-center gap-2">
          <Users aria-hidden="true" className="h-4 w-4 text-blue" />
          {t('admin.title')}
        </CardTitle>
        {(users.data ?? []).map((u) => (
          <p key={u.id} className="flex flex-wrap items-center gap-2 text-sm">
            {u.display_name} ({u.email})
            <Badge tone={u.role === 'admin' ? 'blue' : 'muted'}>{u.role}</Badge>
          </p>
        ))}
      </Card>
      <Card className="space-y-3">
        <CardTitle className="flex items-center gap-2">
          <UserPlus aria-hidden="true" className="h-4 w-4 text-teal" />
          {t('admin.create')}
        </CardTitle>
        <form className="space-y-2" onSubmit={(e) => void onSubmit(e)}>
          <input className="field w-full" value={name} onChange={(e) => setName(e.target.value)} placeholder={t('auth.display_name')} required />
          <input className="field w-full" type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder={t('auth.email')} required />
          <input className="field w-full" type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder={t('auth.password')} required />
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
          <Button type="submit">
            <UserPlus aria-hidden="true" className="h-4 w-4" />
            {t('admin.create')}
          </Button>
        </form>
      </Card>
    </div>
  )
}
