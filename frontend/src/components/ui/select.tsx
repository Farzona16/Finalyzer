import { cn } from '@/lib/utils'

export function Select({
  id,
  label,
  value,
  onChange,
  options,
  className,
}: {
  id?: string
  label: string
  value: string
  onChange: (value: string) => void
  options: { value: string; label: string }[]
  className?: string
}) {
  return (
    <label className={cn('flex flex-col gap-1 text-sm', className)}>
      <span className="text-muted">{label}</span>
      <select
        id={id}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="field"
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  )
}
