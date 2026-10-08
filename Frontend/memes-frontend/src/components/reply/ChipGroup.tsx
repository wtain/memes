import { useEffect, useRef } from "react"
import type { PickerOption } from "../../reply/taxonomy"
import type { AxisName, QueryProfile } from "../../reply/types"
import ValuePicker from "./ValuePicker"

type PendingFocus = { kind: "remove" | "add"; target: string; previousLength: number }

type Props = {
  axis: AxisName
  label: string
  values: QueryProfile["emotion"]
  labelOf: (key: string) => string
  options: PickerOption[]
  maxValues: number
  pickerDisabled?: boolean
  onAdd: (key: string) => void
  onRemove: (key: string) => void
}

export default function ChipGroup({ axis, label, values, labelOf, options, maxValues, pickerDisabled = false, onAdd, onRemove }: Props) {
  const groupRef = useRef<HTMLDivElement>(null)
  const listRef = useRef<HTMLUListElement>(null)
  const pendingFocus = useRef<PendingFocus | null>(null)

  // After a removal, focus moves to a neighbouring chip's remove button, or to the add button.
  // After an add that fills the axis, it moves to the new chip. The request is honoured only
  // once the values actually changed; an unrelated render just discards it.
  useEffect(() => {
    const pending = pendingFocus.current
    if (pending === null) return
    pendingFocus.current = null
    const changed = pending.kind === "remove" ? values.length < pending.previousLength : values.length > pending.previousLength
    if (!changed) return
    const root = groupRef.current
    if (!root) return
    const removeButtons = Array.from(root.querySelectorAll<HTMLButtonElement>("[data-chip-remove]"))
    const chip = pending.target === "" ? undefined : removeButtons.find((b) => b.getAttribute("data-chip-remove") === pending.target)
    const add = root.querySelector<HTMLButtonElement>("[data-add-button] button")
    const candidates: (HTMLElement | null | undefined)[] = [chip, add && !add.disabled ? add : null, removeButtons[0], listRef.current]
    candidates.find((el) => el)?.focus()
  })

  function remove(key: string, index: number) {
    const neighbour = values[index + 1] ?? values[index - 1]
    pendingFocus.current = { kind: "remove", target: neighbour ? neighbour.key : "", previousLength: values.length }
    onRemove(key)
  }

  function add(key: string) {
    if (values.length + 1 >= maxValues) {
      pendingFocus.current = { kind: "add", target: key, previousLength: values.length }
    }
    onAdd(key)
  }

  const chosen = new Set(values.map((value) => value.key))
  const available = options.filter((option) => !chosen.has(option.key))
  const full = values.length >= maxValues

  return (
    <div ref={groupRef} className="mb-3" data-axis={axis}>
      <div className="mb-1 text-sm font-medium text-gray-700">{label}</div>
      <ul ref={listRef} tabIndex={-1} aria-label={label} className="flex flex-wrap items-center gap-2">
        {values.length === 0 && <li className="text-sm text-gray-400">none</li>}
        {values.map((value, index) => (
          <li key={value.key} className="flex items-center gap-1 rounded-full bg-blue-100 py-1 pl-3 pr-1 text-sm">
            <span>{labelOf(value.key)}</span>
            <button
              type="button"
              data-chip-remove={value.key}
              aria-label={`Remove ${label.toLowerCase()}: ${labelOf(value.key)}`}
              onClick={() => remove(value.key, index)}
              className="flex h-11 w-11 items-center justify-center rounded-full hover:bg-blue-200"
            >
              <span aria-hidden="true">×</span>
            </button>
          </li>
        ))}
        {!full && (
          <li data-add-button>
            <ValuePicker axisLabel={label} options={available} disabled={pickerDisabled} onPick={add} />
          </li>
        )}
      </ul>
    </div>
  )
}
