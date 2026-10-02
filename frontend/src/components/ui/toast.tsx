import { CircleAlert, Info, X } from 'lucide-react'
import { create } from 'zustand'

export interface Toast {
  id: string
  title: string
  tone?: 'info' | 'error'
}

interface ToastState {
  items: Toast[]
  push: (title: string, tone?: Toast['tone']) => void
  dismiss: (id: string) => void
}

/** Short-lived messages at the bottom of the screen (spec U8). */
export const useToasts = create<ToastState>((set) => ({
  items: [],
  push: (title, tone = 'info') => {
    const id = Math.random().toString(16).slice(2, 10)
    set((state) => ({ items: [...state.items, { id, title, tone }] }))
    window.setTimeout(() => set((state) => ({ items: state.items.filter((t) => t.id !== id) })), 4000)
  },
  dismiss: (id) => set((state) => ({ items: state.items.filter((t) => t.id !== id) })),
}))

export function ToastViewport() {
  const items = useToasts((s) => s.items)
  const dismiss = useToasts((s) => s.dismiss)
  if (!items.length) return null
  return (
    <div className="fixed bottom-4 right-4 z-50 flex w-80 flex-col gap-2" role="status" aria-live="polite">
      {items.map((item) => {
        const Icon = item.tone === 'error' ? CircleAlert : Info
        return (
          <button
            key={item.id}
            type="button"
            className={`glass glow-hover interactive flex items-start gap-2 p-3 text-left text-sm ${item.tone === 'error' ? 'text-red' : 'text-text'}`}
            onClick={() => dismiss(item.id)}
          >
            <Icon aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />
            <span className="flex-1">{item.title}</span>
            <X aria-hidden="true" className="h-3.5 w-3.5 text-muted" />
          </button>
        )
      })}
    </div>
  )
}
