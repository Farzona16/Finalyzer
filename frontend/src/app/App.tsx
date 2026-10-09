import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useEffect, useState, type ReactNode } from 'react'
import { I18nextProvider, useTranslation } from 'react-i18next'
import { createBrowserRouter, MemoryRouter, Route, RouterProvider, Routes } from 'react-router-dom'

import { ErrorBoundary } from '@/components/ErrorBoundary'
import { ToastViewport } from '@/components/ui/toast'
import { AdminPage } from '@/features/admin/AdminPage'
import { AskPage } from '@/features/ask/AskPage'
import { LoginPage } from '@/features/auth/LoginPage'
import { SetupPage } from '@/features/auth/SetupPage'
import { DashboardPage } from '@/features/dashboard/DashboardPage'
import { DatasetLayout, SummaryPage } from '@/features/dataset/DatasetLayout'
import { HomePage } from '@/features/home/HomePage'
import { NotFoundPage } from '@/features/home/NotFoundPage'
import { InsightsPage } from '@/features/insights/InsightsPage'
import { KpiPage } from '@/features/kpis/KpiPage'
import { PrivacyPage } from '@/features/privacy/PrivacyPage'
import { i18n } from '@/i18n'
import { watchSystemTheme } from '@/theme/store'

import { AppShell } from './AppShell'

function makeQueryClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { staleTime: 30_000, refetchOnWindowFocus: false } } })
}

/** All providers in one place; tests render pages inside it with a memory router. */
export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(makeQueryClient)
  useEffect(() => watchSystemTheme(), [])
  return (
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={client}>
        <ErrorBoundary>
          {children}
          <ToastViewport />
        </ErrorBoundary>
      </QueryClientProvider>
    </I18nextProvider>
  )
}

export function AppRoutes() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<HomePage />} />
        <Route path="login" element={<LoginPage />} />
        <Route path="setup" element={<SetupPage />} />
        <Route path="first-run" element={<SetupPage />} />
        <Route path="privacy" element={<PrivacyPage />} />
        <Route path="admin" element={<AdminPage />} />
        <Route path="datasets/:id" element={<DatasetLayout />}>
          <Route index element={<SummaryPage />} />
          <Route path="dashboard" element={<DashboardPage />} />
          <Route path="insights" element={<InsightsPage />} />
          <Route path="ask" element={<AskPage />} />
          <Route path="kpis" element={<KpiPage />} />
        </Route>
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  )
}

/** For tests. */
export function TestApp({ path = '/' }: { path?: string }) {
  return (
    <Providers>
      <MemoryRouter initialEntries={[path]}>
        <AppRoutes />
      </MemoryRouter>
    </Providers>
  )
}

const router = createBrowserRouter([{ path: '*', element: <AppRoutes /> }])

function WakingGate({ children }: { children: ReactNode }) {
  const { t } = useTranslation()
  const [ready, setReady] = useState(false)
  const [gaveUp, setGaveUp] = useState(false)
  useEffect(() => {
    let cancelled = false
    const started = Date.now()
    const ping = async () => {
      while (!cancelled && Date.now() - started < 60_000) {
        try {
          const response = await fetch('/health', { cache: 'no-store' })
          if (response.ok) {
            if (!cancelled) setReady(true)
            return
          }
        } catch {
          /* cold start */
        }
        await new Promise((resolve) => setTimeout(resolve, 1500))
      }
      if (!cancelled) setGaveUp(true)
    }
    void ping()
    return () => {
      cancelled = true
    }
  }, [])
  if (ready) return children
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-2 p-8 text-center">
      <p className="text-lg font-semibold">{t('app.waking')}</p>
      <p className="text-sm text-muted">{gaveUp ? t('errors.network') : t('app.waking_hint')}</p>
    </div>
  )
}

export function App() {
  return (
    <Providers>
      <WakingGate>
        <RouterProvider router={router} />
      </WakingGate>
    </Providers>
  )
}
