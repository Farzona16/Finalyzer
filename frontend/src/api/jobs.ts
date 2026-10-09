import { get } from '@/api/client'

export interface Job {
  id: string
  kind: string
  status: 'queued' | 'running' | 'done' | 'failed' | 'interrupted'
  progress: number
  message_key: string
  result: Record<string, unknown> | null
  error: { code: string; title: string; detail: string } | null
}

/** Poll a job until it finishes. The UI progress bar reads each snapshot. */
export async function waitJob(
  jobId: string,
  timeoutMs = 120_000,
  onProgress?: (job: Job) => void,
): Promise<Job> {
  const started = Date.now()
  while (Date.now() - started < timeoutMs) {
    const job = await get<Job>(`/jobs/${jobId}`)
    onProgress?.(job)
    if (job.status === 'done' || job.status === 'failed' || job.status === 'interrupted') return job
    await new Promise((resolve) => setTimeout(resolve, 250))
  }
  throw new Error('job timeout')
}
