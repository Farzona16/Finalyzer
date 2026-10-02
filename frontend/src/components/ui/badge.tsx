import type { LucideIcon } from 'lucide-react'
import type { HTMLAttributes } from 'react'

import { cn } from '@/lib/utils'

const TONES = {
  muted: 'text-muted',
  green: 'text-green',
  blue: 'text-blue',
  amber: 'text-amber',
  teal: 'text-teal',
  red: 'text-red',
} as const

export function Badge({
  icon: Icon,
  tone = 'muted',
  className,
  children,
  ...props
}: HTMLAttributes<HTMLSpanElement> & {
  icon?: LucideIcon
  tone?: keyof typeof TONES
}) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full border border-border/40 bg-surface-2/60 px-2 py-0.5',
        'text-xs font-medium transition-all duration-200 ease-in-out',
        TONES[tone],
        className,
      )}
      {...props}
    >
      {Icon ? <Icon aria-hidden="true" className="h-3 w-3" /> : null}
      {children}
    </span>
  )
}
