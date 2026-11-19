import { createContext, useContext } from "react"

export type ToastTone = "success" | "info" | "error"
export type ToastApi = { show: (message: string, tone?: ToastTone) => void }

export const NOOP_TOAST_API: ToastApi = { show: () => {} }
export const ToastContext = createContext<ToastApi>(NOOP_TOAST_API)

/** Outside a ToastProvider this returns a stable no-op. */
export function useToast(): ToastApi {
  return useContext(ToastContext)
}
