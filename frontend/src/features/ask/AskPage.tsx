import { CircleHelp, HelpCircle, Send, ShieldAlert, ShieldCheck, Sparkles } from 'lucide-react'
import { type FormEvent, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'

import { get, post } from '@/api/client'
import { Glossed } from '@/components/Glossed'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Dialog } from '@/components/ui/dialog'
import { Tabs } from '@/components/ui/tabs'
import { currentLocale } from '@/i18n'
import { useSession } from '@/session/store'

interface Answer {
  status: string
  headline: string
  explanation: string
  so_what: string
  next_step: string
  details: string
  verified: boolean
  sql: string | null
  followups: string[]
  options: { label: string; append: string }[]
  table: { columns: string[]; rows: unknown[][]; row_count: number } | null
}

export function AskPage() {
  const { t } = useTranslation()
  const { id = '' } = useParams()
  const lang = currentLocale() === 'en' ? 'en' : 'uz'
  const reading = useSession((s) => s.me?.user.reading_level)
  const [level, setLevel] = useState<'simple' | 'expert'>(reading === 'expert' ? 'expert' : 'simple')
  const [question, setQuestion] = useState('')
  const [answer, setAnswer] = useState<Answer | null>(null)
  const [howOpen, setHowOpen] = useState(false)
  const inflight = useRef(false)
  const suggested = useQuery({
    queryKey: ['suggested', id, lang],
    queryFn: ({ signal }) => get<{ questions: string[] }>(`/datasets/${id}/suggested-questions?lang=${lang}`, signal),
  })

  async function ask(text: string, nextLevel = level) {
    const trimmed = text.trim()
    if (!trimmed || inflight.current) return
    inflight.current = true
    setQuestion(trimmed)
    try {
      const body = await post<Answer>(`/datasets/${id}/ask`, {
        question: trimmed,
        lang,
        level: nextLevel,
      })
      setAnswer(body)
    } finally {
      inflight.current = false
    }
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault()
    if (question.trim()) void ask(question.trim())
  }

  const badge =
    answer?.status === 'clarify'
      ? { tone: 'amber' as const, icon: CircleHelp, label: t('ask.clarification') }
      : answer?.verified
        ? { tone: 'green' as const, icon: ShieldCheck, label: t('ask.verified') }
        : { tone: 'red' as const, icon: ShieldAlert, label: t('ask.unverified') }

  return (
    <div className="space-y-4">
      <Tabs
        value={level}
        onChange={(next) => {
          const value = next as typeof level
          setLevel(value)
          if (question.trim()) void ask(question.trim(), value)
        }}
        items={[
          { id: 'simple', label: t('level.simple'), icon: Sparkles },
          { id: 'expert', label: t('level.expert'), icon: HelpCircle },
        ]}
      />
      <form className="flex gap-2" onSubmit={onSubmit}>
        <input
          className="field flex-1"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder={t('ask.placeholder')}
        />
        <Button type="submit">
          <Send aria-hidden="true" className="h-4 w-4" />
          {t('ask.send')}
        </Button>
      </form>
      <div>
        <h2 className="mb-2 text-sm font-semibold">{t('ask.suggested')}</h2>
        <div className="flex flex-wrap gap-2">
          {(suggested.data?.questions ?? []).map((q) => (
            <Button key={q} variant="outline" size="sm" onClick={() => void ask(q)}>
              {q}
            </Button>
          ))}
        </div>
      </div>
      {answer && (
        <Card className="space-y-2">
          <Badge tone={badge.tone} icon={badge.icon}>
            {badge.label}
          </Badge>
          <p className="font-semibold">
            <Glossed text={answer.headline} />
          </p>
          <p>
            <Glossed text={answer.explanation} />
          </p>
          {answer.so_what ? <p className="text-sm text-muted">{answer.so_what}</p> : null}
          {answer.next_step ? <p className="text-sm">{answer.next_step}</p> : null}
          {level === 'expert' && answer.sql ? (
            <pre className="overflow-auto rounded-lg bg-surface-2/80 p-2 text-xs">{answer.sql}</pre>
          ) : null}
          {answer.table && answer.table.rows.length > 0 && (
            <div className="overflow-auto text-sm">
              <table className="w-full">
                <thead>
                  <tr>
                    {answer.table.columns.map((col) => (
                      <th key={col} className="border-b border-border/40 px-2 py-1 text-left">
                        {col}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {answer.table.rows.slice(0, 20).map((row, i) => (
                    <tr key={i}>
                      {row.map((cell, j) => (
                        <td key={j} className="border-b border-border/40 px-2 py-1">
                          {String(cell ?? '')}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <Button variant="outline" size="sm" onClick={() => setHowOpen(true)}>
            <HelpCircle aria-hidden="true" className="h-4 w-4" />
            {t('ask.how')}
          </Button>
          <Dialog open={howOpen} title={t('ask.how')} onClose={() => setHowOpen(false)}>
            <p className="mb-2 font-medium">{t('ask.reasoning')}</p>
            <p className="whitespace-pre-wrap text-sm">{answer.details}</p>
            {answer.sql ? (
              <>
                <p className="mt-3 font-medium">{t('ask.sql')}</p>
                <pre className="overflow-auto rounded-lg bg-surface-2/80 p-2 text-xs">{answer.sql}</pre>
              </>
            ) : null}
          </Dialog>
          {answer.followups.map((item) => (
            <Button key={item} variant="ghost" size="sm" onClick={() => void ask(item)}>
              {item}
            </Button>
          ))}
          {answer.options.map((opt) => (
            <Button key={opt.append} variant="secondary" size="sm" onClick={() => void ask(`${question} ${opt.append}`)}>
              {opt.label}
            </Button>
          ))}
        </Card>
      )}
    </div>
  )
}
