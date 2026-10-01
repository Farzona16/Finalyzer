import { TOKEN_RE } from '@/lib/glossary'
import { Tooltip } from '@/components/ui/tooltip'

/** Turns `[[key|visible]]` glossary tokens into tooltips; other text is shown as-is. */
export function Glossed({ text }: { text: string }) {
  const parts = text.split(TOKEN_RE)
  return (
    <>
      {parts.map((part, index) => {
        const match = part.match(/^\[\[([^|]+)\|([\s\S]+)\]\]$/)
        if (!match) return <span key={index}>{part}</span>
        return (
          <Tooltip key={index} label={match[1] ?? ''}>
            {match[2]}
          </Tooltip>
        )
      })}
    </>
  )
}
