import { type FormEvent, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'

import { get, post } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Glossed } from '@/components/Glossed'
import { currentLocale } from '@/i18n'

interface Answer {
  status: string
  headline: string
  explanation: string
  verified: boolean
  followups: string[]
  options: { label: string; append: string }[]
}

export function AskPage() {
  const { t } = useTranslation()
  const { id = '' } = useParams()
  const lang = currentLocale() === 'en' ? 'en' : 'uz'
  const [question, setQuestion] = useState('')
  const [answer, setAnswer] = useState<Answer | null>(null)
  const suggested = useQuery({
    queryKey: ['suggested', id, lang],
    queryFn: ({ signal }) => get<{ questions: string[] }>(`/datasets/${id}/suggested-questions?lang=${lang}`, signal),
  })

  async function ask(text: string) {
    setQuestion(text)
    const body = await post<Answer>(`/datasets/${id}/ask`, { question: text, lang })
    setAnswer(body)
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault()
    if (question.trim()) void ask(question.trim())
  }

  return (
    <div className="space-y-4">
      <form className="flex gap-2" onSubmit={onSubmit}>
        <input
          className="h-10 flex-1 rounded-lg border border-border bg-bg px-3"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder={t('ask.placeholder')}
        />
        <Button type="submit">{t('ask.send')}</Button>
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
          <p className="text-xs uppercase tracking-wide">
            {answer.status === 'clarify' ? t('ask.clarification') : answer.verified ? t('ask.verified') : t('ask.unverified')}
          </p>
          <p className="font-semibold">
            <Glossed text={answer.headline} />
          </p>
          <p>
            <Glossed text={answer.explanation} />
          </p>
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
