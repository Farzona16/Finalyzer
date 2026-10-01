import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

export function NotFoundPage() {
  const { t } = useTranslation()
  return (
    <div className="space-y-3 py-10">
      <p className="text-lg">{t('errors.not_found')}</p>
      <Link to="/" className="text-blue underline">
        {t('nav.home')}
      </Link>
    </div>
  )
}
