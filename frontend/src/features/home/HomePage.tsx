import {
  CheckCircle2,
  CircleAlert,
  Database,
  FolderOpen,
  Loader2,
  ServerCrash,
  Upload,
} from 'lucide-react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate } from 'react-router-dom'

import { errorMessage, get, post, postForm } from '@/api/client'
import { type Job, waitJob } from '@/api/jobs'
import { Badge } from '@/components/ui/badge'
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

function StatusIcon({ ok, down }: { ok: boolean; down: boolean }) {
  if (down) return <ServerCrash aria-hidden="true" className="h-4 w-4 text-red" />
  if (ok) return <CheckCircle2 aria-hidden="true" className="h-4 w-4 text-green" />
  return <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin text-teal" />
}

function datasetTone(status: string): 'green' | 'red' | 'amber' | 'muted' {
  if (status === 'ready') return 'green'
  if (status === 'failed') return 'red'
  if (status === 'building' || status === 'profiling') return 'amber'
  return 'muted'
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
    if (done.status === 'failed' || done.status === 'interrupted') {
      throw new Error(done.error?.detail || t(`jobs.${done.status}`))
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
        <h1 className="text-3xl font-semibold tracking-tight">{t('home.title')}</h1>
        <p className="mt-1 text-muted">{t('home.subtitle')}</p>
      </div>
      <Card className="max-w-md space-y-2">
        <CardTitle>{t('app.name')}</CardTitle>
        <p aria-live="polite" className="flex items-center gap-2">
          <StatusIcon ok={!!status.data} down={status.isError} />
          {status.isPending && t('common.loading')}
          {status.isError && t('home.status.down')}
          {status.data && t('home.status.ok', { version: status.data.version })}
        </p>
        <p className="text-sm text-muted">{t('home.letters_check')}</p>
        {!me.data && (
          <p className="text-sm">
            <Link to="/login" className="text-blue underline transition-all duration-200 ease-in-out hover:brightness-110">
              {t('auth.login')}
            </Link>
            {' · '}
            <Link to="/setup" className="text-blue underline transition-all duration-200 ease-in-out hover:brightness-110">
              {t('auth.first_admin')}
            </Link>
          </p>
        )}
      </Card>
      {job && (
        <Card className="max-w-md space-y-2" aria-live="polite">
          <p className="flex items-center gap-2 text-sm">
            <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin text-teal" />
            {t('jobs.progress', { label: t(`jobs.${job.message_key.split('.').pop() ?? 'running'}`), n: job.progress })}
          </p>
          <div className="h-1.5 overflow-hidden rounded-full bg-surface-2">
            <div
              className="h-full rounded-full bg-teal transition-all duration-200 ease-in-out"
              style={{ width: `${job.progress}%` }}
            />
          </div>
        </Card>
      )}
      {me.data && (
        <>
          <section className="space-y-3">
            <h2 className="flex items-center gap-2 text-lg font-semibold">
              <Database aria-hidden="true" className="h-5 w-5 text-blue" />
              {t('home.demos')}
            </h2>
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
            <h2 className="flex items-center gap-2 text-lg font-semibold">
              <Upload aria-hidden="true" className="h-5 w-5 text-teal" />
              {t('home.upload')}
            </h2>
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
                  <Upload aria-hidden="true" className="h-4 w-4" />
                  {t('home.upload')}
                </Button>
                <p className="text-sm text-muted">{t('home.upload_hint')}</p>
              </>
            ) : (
              <p className="text-sm text-muted">{t('home.viewer_hint')}</p>
            )}
          </section>
          <section className="space-y-2">
            <h2 className="flex items-center gap-2 text-lg font-semibold">
              <FolderOpen aria-hidden="true" className="h-5 w-5 text-green" />
              {t('nav.datasets')}
            </h2>
            {(Array.isArray(datasets.data) ? datasets.data : []).map((row) => (
              <Link
                key={row.id}
                to={`/datasets/${row.id}`}
                className="interactive glass glow-hover flex items-center justify-between gap-3 rounded-card px-4 py-3 text-sm transition-all duration-200 ease-in-out"
              >
                <span className="font-medium text-text">{row.name}</span>
                <Badge
                  tone={datasetTone(row.status)}
                  icon={row.status === 'failed' ? CircleAlert : row.status === 'ready' ? CheckCircle2 : Loader2}
                >
                  {row.status}
                </Badge>
              </Link>
            ))}
          </section>
        </>
      )}
    </div>
  )
}
