import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'
import { LANGUAGES, setLanguage, type Language } from '@/i18n'

/** UZ | EN switch (spec 5.1). It changes every visible string at once. */
export function LanguageSwitch() {
  const { t, i18n } = useTranslation()
  return (
    <div role="group" aria-label={t('language.label')} className="flex items-center gap-1">
      {LANGUAGES.map((language: Language) => (
        <Button
          key={language}
          variant={i18n.language === language ? 'secondary' : 'ghost'}
          size="sm"
          aria-pressed={i18n.language === language}
          lang={language}
          onClick={() => void setLanguage(language)}
        >
          {t(`language.${language}`)}
        </Button>
      ))}
    </div>
  )
}
