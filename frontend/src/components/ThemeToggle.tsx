import { Monitor, Moon, Sun } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'
import { THEME_PREFERENCES, useThemeStore, type ThemePreference } from '@/theme/store'

const ICONS = { light: Sun, dark: Moon, system: Monitor } as const

/** Sun / moon / auto control (spec 9.1). */
export function ThemeToggle() {
  const { t } = useTranslation()
  const preference = useThemeStore((state) => state.preference)
  const setPreference = useThemeStore((state) => state.setPreference)
  return (
    <div role="group" aria-label={t('theme.label')} className="flex items-center gap-1">
      {THEME_PREFERENCES.map((option: ThemePreference) => {
        const Icon = ICONS[option]
        return (
          <Button
            key={option}
            variant={preference === option ? 'secondary' : 'ghost'}
            size="icon"
            aria-pressed={preference === option}
            aria-label={t(`theme.${option}`)}
            title={t(`theme.${option}`)}
            onClick={() => setPreference(option)}
          >
            <Icon aria-hidden="true" className="h-4 w-4" />
          </Button>
        )
      })}
    </div>
  )
}
