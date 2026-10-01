import { useId, type ReactNode } from 'react'

import { cn } from '@/lib/utils'

/** Native title tooltip; glossary entries also show the plain-language explanation. */
export function Tooltip({ label, children, className }: { label: string; children: ReactNode; className?: string }) {
  const id = useId()
  return (
    <span className={cn('relative inline-flex', className)}>
      <abbr title={label} aria-describedby={id} className="cursor-help decoration-dotted underline-offset-2">
        {children}
      </abbr>
      <span id={id} className="sr-only">
        {label}
      </span>
    </span>
  )
}
