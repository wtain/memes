import { useRef } from "react"
import { panelId, tabId } from "./tabIds"

export type TabItem = { id: string; label: string }

const TAB_BASE =
  "relative -mb-px inline-flex min-h-9 items-center px-3 text-sm font-medium cursor-pointer border-b-2 motion-safe:transition-colors pointer-coarse:min-h-11"
const TAB_SELECTED = "border-brand text-fg"
const TAB_IDLE = "border-transparent text-fg-muted hover:text-fg"

/**
 * The tablist of the WAI-ARIA tabs pattern with automatic activation. The caller renders the
 * `role="tabpanel"` elements with `id={panelId(...)}`, `aria-labelledby={tabId(...)}` and `hidden`
 * when inactive.
 */
export function Tabs({
  tabs,
  value,
  onChange,
  idPrefix,
  "aria-label": ariaLabel,
}: {
  tabs: TabItem[]
  value: string
  onChange: (id: string) => void
  idPrefix: string
  "aria-label"?: string
}) {
  const refs = useRef<Record<string, HTMLButtonElement | null>>({})
  // A value that matches no tab leaves the first tab as the tablist's tab stop.
  const focusable = Math.max(0, tabs.findIndex(t => t.id === value))

  function onKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    if (e.altKey || e.ctrlKey || e.metaKey) return
    if (tabs.length === 0) return
    let next: number
    switch (e.key) {
      case "ArrowRight":
        next = (focusable + 1) % tabs.length
        break
      case "ArrowLeft":
        next = (focusable - 1 + tabs.length) % tabs.length
        break
      case "Home":
        next = 0
        break
      case "End":
        next = tabs.length - 1
        break
      default:
        return
    }
    e.preventDefault()
    e.stopPropagation()
    const target = tabs[next]
    onChange(target.id)
    refs.current[target.id]?.focus()
  }

  return (
    <div role="tablist" aria-label={ariaLabel} onKeyDown={onKeyDown} className="flex gap-1 border-b border-line">
      {tabs.map((t, i) => {
        const selected = t.id === value
        return (
          <button
            key={t.id}
            ref={el => {
              refs.current[t.id] = el
            }}
            type="button"
            role="tab"
            id={tabId(idPrefix, t.id)}
            aria-selected={selected}
            aria-controls={panelId(idPrefix, t.id)}
            tabIndex={i === focusable ? 0 : -1}
            onClick={() => onChange(t.id)}
            className={[TAB_BASE, selected ? TAB_SELECTED : TAB_IDLE].join(" ")}
          >
            {t.label}
          </button>
        )
      })}
    </div>
  )
}
