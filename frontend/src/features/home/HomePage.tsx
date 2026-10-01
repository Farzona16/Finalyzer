import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate } from 'react-router-dom'

import { errorMessage, get, post, postForm } from '@/api/client'
import { type Job, waitJob } from '@/api/jobs'
import { Button } from '@/components/ui/button'
import { Card, CardTitle } from '@/components/ui/card'
import { useToasts } from '@/components/ui/toast'
import { useMeQuery } from '@/session/store'

interface Version {
  version: string
  first_run_required: boolean
}

interface Demo {
  name: string
  domain: string
  tables: string[]
}

interface Dataset {
  id: string
  name: string
  status: string
}

export function HomePage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const push = useToasts((s) => s.push)
  const client = useQueryClient()
  const fileRef = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)
  const [job, setJob] = useState<Job | null>(null)
  const me = useMeQuery()
  const canUpload = me.data?.permissions.includes('upload') ?? false
  const status = useQuery({
    queryKey: ['version'],
    queryFn: ({ signal }) => get<Version>('/version', signal),
    retry: false,
  })
  const demos = useQuery({
    queryKey: ['demos'],
    queryFn: ({ signal }) => get<Demo[]>('/demo', signal),
    enabled: !!me.data,
    retry: false,
  })
  const datasets = useQuery({
    queryKey: ['datasets'],
    queryFn: ({ signal }) => get<Dataset[]>('/datasets', signal),
    enabled: !!me.data,
    retry: false,
  })

  async function track(jobId: string): Promise<Job> {
    const done = await waitJob(jobId, 120_000, setJob)
    if (done.status === 'failed') {
      throw new Error(done.error?.detail || t('jobs.failed'))
    }
    return done
  }

  async function loadDemo(name: string) {
    setBusy(true)
    try {
      const started = await post<{ id: string }>(`/demo/${name}/load`)
      const loaded = await track(started.id)
      const datasetId = (loaded.result as { dataset_id: string }).dataset_id
      const build = await post<{ id: string }>(`/datasets/${datasetId}/build`)
      await track(build.id)
      await client.invalidateQueries({ queryKey: ['datasets'] })
      navigate(`/datasets/${datasetId}/dashboard`)
    } catch (error) {
      push(errorMessage(error), 'error')
    } finally {
      setBusy(false)
      setJob(null)
    }
  }

  async function upload(fileList: FileList | null) {
    if (!fileList?.length) return
    setBusy(true)
    try {
      const body = new FormData()
      for (const file of fileList) body.append('files', file)
      const started = await postForm<{ id: string }>('/datasets', body)
      const done = await track(started.id)
      const datasetId = (done.result as { dataset_id: string }).dataset_id
      navigate(`/datasets/${datasetId}`)
    } catch (error) {
      push(errorMessage(error), 'error')
    } finally {
      setBusy(false)
      setJob(null)
    }
  }

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
        {!me.data && (
          <p className="text-sm">
            <Link to="/login" className="text-blue underline">
              {t('auth.login')}
            </Link>
            {' · '}
            <Link to="/setup" className="text-blue underline">
              {t('auth.first_admin')}
            </Link>
          </p>
        )}
      </Card>
      {job && (
        <p aria-live="polite">
          {t('jobs.progress', { label: t(`jobs.${job.message_key.split('.').pop() ?? 'running'}`), n: job.progress })}
        </p>
      )}
      {me.data && (
        <>
          <section className="space-y-3">
            <h2 className="text-lg font-semibold">{t('home.demos')}</h2>
            <div className="grid gap-3 sm:grid-cols-2">
              {(Array.isArray(demos.data) ? demos.data : []).map((demo) => (
                <Card key={demo.name} className="space-y-2">
                  <CardTitle>{demo.name}</CardTitle>
                  <p className="text-sm text-muted">{demo.tables.join(', ')}</p>
                  {canUpload && (
                    <Button disabled={busy} onClick={() => void loadDemo(demo.name)}>
                      {demo.name}
                    </Button>
                  )}
                </Card>
              ))}
            </div>
          </section>
          <section className="space-y-3">
            <h2 className="text-lg font-semibold">{t('home.upload')}</h2>
            {canUpload ? (
              <>
                <input
                  ref={fileRef}
                  type="file"
                  accept=".csv,.xlsx"
                  multiple
                  className="hidden"
                  onChange={(e) => void upload(e.target.files)}
                />
                <Button disabled={busy} onClick={() => fileRef.current?.click()}>
                  {t('home.upload')}
                </Button>
              </>
            ) : (
              <p className="text-sm text-muted">{t('home.viewer_hint')}</p>
            )}
          </section>
          <section className="space-y-2">
            <h2 className="text-lg font-semibold">{t('nav.datasets')}</h2>
            {(Array.isArray(datasets.data) ? datasets.data : []).map((row) => (
              <Link key={row.id} to={`/datasets/${row.id}`} className="block text-blue underline">
                {row.name} ({row.status})
              </Link>
            ))}
          </section>
        </>
      )}
    </div>
  )
}
