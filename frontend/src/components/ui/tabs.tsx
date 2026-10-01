import { cn } from '@/lib/utils'

export function Tabs({
  value,
  onChange,
  items,
}: {
  value: string
  onChange: (id: string) => void
  items: { id: string; label: string }[]
}) {
  return (
    <div role="tablist" className="flex flex-wrap gap-1 border-b border-border">
      {items.map((item) => (
        <button
          key={item.id}
          type="button"
          role="tab"
          aria-selected={value === item.id}
          className={cn(
            'rounded-t-lg px-3 py-2 text-sm',
            value === item.id ? 'bg-surface-2 text-text' : 'text-muted hover:text-text',
          )}
          onClick={() => onChange(item.id)}
        >
          {item.label}
        </button>
      ))}
    </div>
  )
}
