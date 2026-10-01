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
      {items.map((item) => (
        <button
          key={item.id}
          type="button"
          className={`rounded-lg border border-border bg-surface p-3 text-left text-sm shadow ${item.tone === 'error' ? 'text-red' : 'text-text'}`}
          onClick={() => dismiss(item.id)}
        >
          {item.title}
        </button>
      ))}
    </div>
  )
}
