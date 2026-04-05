import { useEffect, ReactNode } from "react"
import { createPortal } from "react-dom"

type Props = {
  children: ReactNode
  onClose: () => void
  title?: string
}

export function Modal({ children, onClose, title }: Props) {
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
      className="fixed inset-0 bg-black bg-opacity-60 flex items-center justify-center z-50 p-6"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-xl shadow-xl max-w-5xl w-full max-h-[90vh] overflow-y-auto p-6"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex justify-between mb-4">
          <h2 className="text-xl font-bold">
            {title ?? "Details"}
          </h2>
          <button onClick={onClose} className="cursor-pointer">✕</button>
        </div>

        {children}
      </div>
    </div>,
    document.body
  )
}