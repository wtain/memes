import { useEffect, useRef, useState } from "react"
import { ToastContext, type ToastTone } from "./toastContext"
import { ToastTimers } from "./toastTimers"

type ToastItem = { id: number; message: string; tone: ToastTone }

const MAX_TOASTS = 3
const DURATION_MS: Record<ToastTone, number> = { success: 2500, info: 2500, error: 6000 }

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([])
  const [timers] = useState(() => new ToastTimers())
  const unmounted = useRef(false)

  useEffect(() => {
    unmounted.current = false
    return () => {
      unmounted.current = true
      timers.clear()
    }
  }, [timers])

  function remove(id: number) {
    timers.delete(id)
    if (!unmounted.current) setToasts((list) => list.filter((t) => t.id !== id))
  }

  function show(message: string, tone: ToastTone = "success") {
    const id = timers.nextId()
    timers.add(id, DURATION_MS[tone], () => remove(id))
    setToasts((list) => [...list, { id, message, tone }].slice(-MAX_TOASTS))
    while (timers.size > MAX_TOASTS) {
      const oldest = timers.oldest()
      if (oldest === undefined) break
      timers.delete(oldest)
    }
  }

  const status = toasts.filter((t) => t.tone !== "error")
  const alerts = toasts.filter((t) => t.tone === "error")

  function renderToast(t: ToastItem) {
    return (
      <div
        key={t.id}
        data-toast
        onMouseEnter={() => timers.pause(t.id)}
        onMouseLeave={() => timers.resume(t.id, () => remove(t.id))}
        onFocus={() => timers.pause(t.id)}
        onBlur={() => timers.resume(t.id, () => remove(t.id))}
        className="pointer-events-auto flex items-center gap-3 rounded-lg bg-surface px-4 py-2 text-sm text-fg shadow-lg ring-1 ring-black/10 motion-safe:transition-opacity"
      >
        <span>{t.message}</span>
        {t.tone === "error" && (
          <button type="button" aria-label="Dismiss" onClick={() => remove(t.id)} className="rounded px-1 text-fg">
            ×
          </button>
        )}
      </div>
    )
  }

  return (
    <ToastContext.Provider value={{ show }}>
      {children}
      <div className="pointer-events-none fixed inset-x-0 bottom-0 z-[60] flex flex-col items-center gap-2 px-4 pb-[env(safe-area-inset-bottom)]">
        <div role="status" aria-live="polite" className="flex flex-col items-center gap-2">
          {status.map(renderToast)}
        </div>
        <div role="alert" className="flex flex-col items-center gap-2">
          {alerts.map(renderToast)}
        </div>
      </div>
    </ToastContext.Provider>
  )
}
