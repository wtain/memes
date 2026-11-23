import { useEffect, type ReactNode } from "react"
import { createPortal } from "react-dom"

type Props = {
  children: ReactNode
  onClose: () => void
  title?: string
  /** "wide" opts into the wide quick-look container; the default is unchanged. */
  size?: "default" | "wide"
  /** Keeps the heading for screen readers only; the header row then holds just the close button. */
  titleHidden?: boolean
}

export function Modal({ children, onClose, title, size = "default", titleHidden = false }: Props) {
  // ESC close
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
    }

    document.addEventListener("keydown", handler)
    return () => document.removeEventListener("keydown", handler)
  }, [onClose])

  // Lock scroll
  useEffect(() => {
    document.body.style.overflow = "hidden"
    return () => {
      document.body.style.overflow = "auto"
    }
  }, [])

  return createPortal(
    <div
      className="fixed inset-0 bg-scrim flex items-center justify-center z-50 p-6"
      onClick={onClose}
    >
      <div
        className={`bg-white rounded-xl shadow-xl w-full max-h-[90vh] overflow-y-auto p-6 ${
          size === "wide" ? "max-w-7xl lg:overflow-hidden" : "max-w-5xl"
        }`}
        onClick={e => e.stopPropagation()}
      >
        <div className={titleHidden ? "flex justify-end mb-2" : "flex justify-between mb-4"}>
          <h2 className={titleHidden ? "sr-only" : "text-xl font-bold"}>
            {title ?? "Details"}
          </h2>
          <button onClick={onClose} aria-label={titleHidden ? "Close" : undefined} className="cursor-pointer">✕</button>
        </div>

        {children}
      </div>
    </div>,
    document.body
  )
}