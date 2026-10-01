import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useEffect, useState, type ReactNode } from 'react'
import { I18nextProvider } from 'react-i18next'
import { createBrowserRouter, MemoryRouter, Route, RouterProvider, Routes } from 'react-router-dom'

import { ErrorBoundary } from '@/components/ErrorBoundary'
import { HomePage } from '@/features/home/HomePage'
import { NotFoundPage } from '@/features/home/NotFoundPage'
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
        <ErrorBoundary>{children}</ErrorBoundary>
      </QueryClientProvider>
    </I18nextProvider>
  )
}

export function AppRoutes() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<HomePage />} />
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

export function App() {
  return (
    <Providers>
      <RouterProvider router={router} />
    </Providers>
  )
}
