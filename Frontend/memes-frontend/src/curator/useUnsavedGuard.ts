import { useEffect } from "react"
import { useBlocker } from "react-router-dom"
import type { Blocker } from "react-router-dom"

/** Blocks in-app navigation and a page unload while `active` (unsaved edits). */
export function useUnsavedGuard(active: boolean): Blocker {
  const blocker = useBlocker(active)

  useEffect(() => {
    if (!active) return
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = ""
    }
    window.addEventListener("beforeunload", onBeforeUnload)
    return () => window.removeEventListener("beforeunload", onBeforeUnload)
  }, [active])

  return blocker
}
