import { X } from 'lucide-react'
import { type ReactNode, useEffect, useId, useRef } from 'react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

export function Dialog({
  open,
  title,
  onClose,
  children,
  className,
}: {
  open: boolean
  title: string
  onClose: () => void
  children: ReactNode
  className?: string
}) {
  const { t } = useTranslation()
  const headingId = useId()
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const node = ref.current
    if (!node) return
    if (open && !node.open) node.showModal()
    if (!open && node.open) node.close()
  }, [open])
  return (
    <dialog
      ref={ref}
      aria-labelledby={headingId}
      className={cn('glass rounded-card p-5 text-text shadow-glow backdrop:bg-black/50', className)}
      onClose={onClose}
    >
      <div className="mb-3 flex items-start justify-between gap-3">
        <h2 id={headingId} className="text-lg font-semibold">
          {title}
        </h2>
        <Button variant="ghost" size="icon" onClick={onClose} aria-label={t('common.close')}>
          <X aria-hidden="true" className="h-4 w-4" />
        </Button>
      </div>
      {children}
      <div className="mt-3">
        <Button variant="ghost" size="sm" onClick={onClose}>
          {t('common.cancel')}
        </Button>
      </div>
    </dialog>
  )
}
