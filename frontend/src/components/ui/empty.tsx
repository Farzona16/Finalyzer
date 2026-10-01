import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

export function EmptyState({ title, hint, className }: { title: string; hint?: string; className?: string }) {
  return (
    <div className={cn('rounded-card border border-dashed border-border p-8 text-center text-muted', className)}>
      <p className="text-text">{title}</p>
      {hint ? <p className="mt-1 text-sm">{hint}</p> : null}
    </div>
  )
}

export function EmptyWithAction({ title, hint, children }: { title: string; hint?: string; children?: ReactNode }) {
  return (
    <div className="space-y-3">
      <EmptyState title={title} hint={hint} />
      {children}
    </div>
  )
}
