import { cn } from '@/lib/utils'

const TONE: Record<string, string> = {
  good: 'stroke-green',
  bad: 'stroke-red',
  neutral: 'stroke-teal',
}

/** Circular indicator for a 0–100 percent KPI. */
export function Gauge({
  value,
  tone = 'neutral',
  label,
}: {
  value: number | null
  tone?: string
  label: string
}) {
  const pct = Math.max(0, Math.min(100, value ?? 0))
  const radius = 36
  const circ = 2 * Math.PI * radius
  const arc = circ * 0.75
  const offset = arc - (pct / 100) * arc
  return (
    <div className="relative mx-auto h-24 w-24" role="img" aria-label={label}>
      <svg viewBox="0 0 100 100" className="h-full w-full -rotate-[135deg]">
        <circle
          cx="50"
          cy="50"
          r={radius}
          fill="none"
          strokeWidth="8"
          className="stroke-surface-2"
          strokeDasharray={`${arc} ${circ}`}
          strokeLinecap="round"
        />
        <circle
          cx="50"
          cy="50"
          r={radius}
          fill="none"
          strokeWidth="8"
          className={cn('transition-all duration-200 ease-in-out', TONE[tone] ?? TONE.neutral)}
          strokeDasharray={`${arc} ${circ}`}
          strokeDashoffset={offset}
          strokeLinecap="round"
        />
      </svg>
    </div>
  )
}
