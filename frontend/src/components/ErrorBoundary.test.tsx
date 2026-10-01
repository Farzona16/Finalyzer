import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { I18nextProvider } from 'react-i18next'
import { describe, expect, it, vi } from 'vitest'

import { i18n } from '@/i18n'

import { ErrorBoundary } from './ErrorBoundary'

let explode = true
function Bomb() {
  if (explode) throw new Error('secret customer data 12345')
  return <p>fine</p>
}

describe('ErrorBoundary', () => {
  it('shows a plain message and a reference, never the error text, and can recover', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined)
    render(
      <I18nextProvider i18n={i18n}>
        <ErrorBoundary>
          <Bomb />
        </ErrorBoundary>
      </I18nextProvider>,
    )
    const alert = screen.getByRole('alert')
    expect(alert.textContent).not.toContain('secret')
    expect(alert.textContent).toMatch(/[0-9a-f]{6,8}/)
    explode = false
    await userEvent.click(screen.getByRole('button'))
    expect(screen.getByText('fine')).toBeTruthy()
  })
})
