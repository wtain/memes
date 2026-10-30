import { useState } from "react"
import { isValidBox, MIN_SIDE, parseCoordinate, sameBox } from "../../recut/boxEdit"
import type { Box, Size } from "../../recut/boxOverlay"

type Props = {
  draft: Box
  size: Size
  current: Box
  /** The detector's box once the proposal was adjusted before; null otherwise. */
  detectedBox: Box | null
  disabled: boolean
  onChange: (box: Box) => void
  onApply: () => void
  onCancel: () => void
  onReset: () => void
}

const FIELDS = ["x", "y", "w", "h"] as const
type Field = (typeof FIELDS)[number]

/** Numeric twin of the drag editor: exact values and keyboard entry, plus Apply / Cancel / Reset. A field shows what is being
 * typed while that is not a whole number yet (an empty field mid-edit), and the draft value otherwise. */
export default function AdjustPanel({ draft, size, current, detectedBox, disabled, onChange, onApply, onCancel, onReset }: Props) {
  const [typed, setTyped] = useState<Partial<Record<Field, string>>>({})

  const shown = (field: Field): string => {
    const text = typed[field]
    return text !== undefined && (parseCoordinate(text) === null || parseCoordinate(text) === draft[field]) ? text : String(draft[field])
  }
  const incomplete = FIELDS.some((field) => typed[field] !== undefined && parseCoordinate(typed[field] as string) === null)
  const canApply = !disabled && !incomplete && isValidBox(draft, size) && !sameBox(draft, current)
  const canReset = detectedBox !== null && !sameBox(detectedBox, current)

  function edit(field: Field, text: string) {
    setTyped((previous) => ({ ...previous, [field]: text }))
    const value = parseCoordinate(text)
    if (value !== null) onChange({ ...draft, [field]: value })
  }

  function settle(field: Field) {
    setTyped((previous) => {
      const next = { ...previous }
      delete next[field]
      return next
    })
  }

  return (
    <form className="mt-4 rounded border p-3" onSubmit={(event) => { event.preventDefault(); if (canApply) onApply() }}>
      <div className="flex flex-wrap items-end gap-3">
        {FIELDS.map((field) => (
          <label key={field} className="flex flex-col text-xs text-gray-600">
            <span>{field}</span>
            <input
              type="number"
              inputMode="numeric"
              min={0}
              value={shown(field)}
              disabled={disabled}
              onChange={(event) => edit(field, event.target.value)}
              onBlur={() => settle(field)}
              className="min-h-11 w-24 rounded border px-2 text-sm text-black"
            />
          </label>
        ))}
      </div>
      <p className="mt-2 text-xs text-gray-600">
        Pixels of the source ({size.width} x {size.height}). Each side at least {MIN_SIDE}, the box inside the image.
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button type="submit" disabled={!canApply} className="min-h-11 rounded border px-3 disabled:opacity-50">Apply</button>
        <button type="button" disabled={disabled} onClick={onCancel} className="min-h-11 rounded border px-3 disabled:opacity-50">Cancel</button>
        {canReset && (
          <button type="button" disabled={disabled} onClick={onReset} className="min-h-11 rounded border px-3 disabled:opacity-50">
            Reset to detected box
          </button>
        )}
      </div>
    </form>
  )
}
