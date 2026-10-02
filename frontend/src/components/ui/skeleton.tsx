import { cn } from '@/lib/utils'

export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      className={cn('animate-pulse rounded-lg bg-surface-2/80 shadow-inner', className)}
      aria-hidden="true"
    />
  )
}
