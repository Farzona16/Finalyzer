import { Component, type ErrorInfo, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'

function Fallback({ onRetry, reference }: { onRetry: () => void; reference: string }) {
  const { t } = useTranslation()
  return (
    <Card role="alert" className="mx-auto mt-16 max-w-md space-y-3">
      <p>{t('errors.generic')}</p>
      <p className="text-sm text-muted">{t('errors.reference', { id: reference })}</p>
      <Button onClick={onRetry}>{t('common.retry')}</Button>
    </Card>
  )
}

interface State {
  failed: boolean
  reference: string
}

/** Catches render errors; shows a plain message and a reference id, never the exception text. */
export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  override state: State = { failed: false, reference: '' }

  static getDerivedStateFromError(): State {
    return { failed: true, reference: Math.random().toString(16).slice(2, 10) }
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    // Type and component stack only: a message can quote data (spec S16).
    console.error('render_error', error.name, info.componentStack)
  }

  override render(): ReactNode {
    if (!this.state.failed) return this.props.children
    return <Fallback reference={this.state.reference} onRetry={() => this.setState({ failed: false })} />
  }
}
