import { BarChart3, Home, LogOut, Shield, Users } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { NavLink, Outlet } from 'react-router-dom'

import { LanguageSwitch } from '@/components/LanguageSwitch'
import { ThemeToggle } from '@/components/ThemeToggle'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { signOut, useMeQuery, useSession } from '@/session/store'

const JOB_KEYS = ['queued', 'running', 'reading', 'cleaning', 'profiling', 'modeling', 'saving', 'building', 'autopilot', 'done', 'failed'] as const

const navClass = ({ isActive }: { isActive: boolean }) =>
  cn(
    'interactive inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm',
    'transition-all duration-200 ease-in-out',
    isActive ? 'bg-surface-2 text-text shadow-glow' : 'text-muted hover:bg-surface-2/60 hover:text-text',
  )

/** Top bar with the language and theme switches, navigation and the page outlet. */
export function AppShell() {
  const { t } = useTranslation()
  useMeQuery()
  const user = useSession((s) => s.me)
  return (
    <div className="flex min-h-screen flex-col">
      <span className="sr-only">{JOB_KEYS.map((k) => t(`jobs.${k}`)).join(' ')}</span>
      <header className="glass sticky top-0 z-40 flex flex-wrap items-center gap-4 border-b border-border/40 px-5 py-3">
        <div className="flex items-center gap-2">
          <BarChart3 aria-hidden="true" className="h-5 w-5 text-teal drop-shadow-[0_0_8px_color-mix(in_srgb,var(--teal)_50%,transparent)]" />
          <span className="text-base font-semibold tracking-tight">{t('app.name')}</span>
          <span className="hidden text-sm text-muted md:inline">{t('app.tagline')}</span>
        </div>
        <nav aria-label={t('nav.menu')} className="flex items-center gap-1">
          <NavLink to="/" end className={navClass}>
            <Home aria-hidden="true" className="h-4 w-4" />
            {t('nav.home')}
          </NavLink>
          {user && (
            <>
              <NavLink to="/privacy" className={navClass}>
                <Shield aria-hidden="true" className="h-4 w-4" />
                {t('nav.privacy')}
              </NavLink>
              {user.user.role === 'admin' && (
                <NavLink to="/admin" className={navClass}>
                  <Users aria-hidden="true" className="h-4 w-4" />
                  {t('nav.admin')}
                </NavLink>
              )}
            </>
          )}
        </nav>
        <div className="ml-auto flex items-center gap-4">
          <span className="sr-only">{t('common.save')}</span>
          <LanguageSwitch />
          <ThemeToggle />
          {user && (
            <Button variant="ghost" size="sm" onClick={() => void signOut()}>
              <LogOut aria-hidden="true" className="h-4 w-4" />
              {t('auth.logout')}
            </Button>
          )}
        </div>
      </header>
      <main className="mx-auto w-full max-w-6xl flex-1 p-5">
        <Outlet />
      </main>
    </div>
  )
}
