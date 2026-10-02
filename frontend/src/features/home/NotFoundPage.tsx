import { Home, SearchX } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

export function NotFoundPage() {
  const { t } = useTranslation()
  return (
    <div className="space-y-3 py-10">
      <p className="flex items-center gap-2 text-lg">
        <SearchX aria-hidden="true" className="h-5 w-5 text-amber" />
        {t('errors.not_found')}
      </p>
      <Link
        to="/"
        className="inline-flex items-center gap-1.5 text-blue underline transition-all duration-200 ease-in-out hover:brightness-110"
      >
        <Home aria-hidden="true" className="h-4 w-4" />
        {t('nav.home')}
      </Link>
    </div>
  )
}
