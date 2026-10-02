import type { LucideIcon } from 'lucide-react'

import { cn } from '@/lib/utils'

export function Tabs({
  value,
  onChange,
  items,
}: {
  value: string
  onChange: (id: string) => void
  items: { id: string; label: string; icon?: LucideIcon }[]
}) {
  return (
    <div role="tablist" className="flex flex-wrap gap-1 border-b border-border/40">
      {items.map((item) => {
        const Icon = item.icon
        const selected = value === item.id
        return (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={selected}
            className={cn(
              'interactive inline-flex items-center gap-1.5 rounded-t-lg px-3 py-2 text-sm',
              'transition-all duration-200 ease-in-out',
              selected ? 'bg-surface-2 text-text shadow-glow' : 'text-muted hover:text-text',
            )}
            onClick={() => onChange(item.id)}
          >
            {Icon ? <Icon aria-hidden="true" className="h-4 w-4" /> : null}
            {item.label}
          </button>
        )
      })}
    </div>
  )
}
