import { useEffect, useId, useRef, useState } from "react"
import type { PickerOption } from "../../reply/taxonomy"

type Props = {
  axisLabel: string
  options: PickerOption[]
  disabled?: boolean
  onPick: (key: string) => void
}

export default function ValuePicker({ axisLabel, options, disabled = false, onPick }: Props) {
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const buttonRef = useRef<HTMLButtonElement>(null)
  const listRef = useRef<HTMLUListElement>(null)
  const listId = useId()

  useEffect(() => {
    if (open) listRef.current?.focus()
  }, [open])

  function close() {
    setOpen(false)
    buttonRef.current?.focus()
  }

  function pick(key: string) {
    onPick(key)
    close()
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLUListElement>) {
    if (e.key === "ArrowDown") {
      e.preventDefault()
      setActive((i) => Math.min(i + 1, options.length - 1))
    } else if (e.key === "ArrowUp") {
      e.preventDefault()
      setActive((i) => Math.max(i - 1, 0))
    } else if (e.key === "Enter") {
      e.preventDefault()
      const option = options[active]
      if (option) pick(option.key)
    } else if (e.key === "Escape") {
      e.preventDefault()
      close()
    }
  }

  return (
    <span className="relative inline-block">
      <button
        ref={buttonRef}
        type="button"
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        onClick={() => {
          setActive(0)
          setOpen((o) => !o)
        }}
        className="min-h-11 rounded-full border border-dashed px-3 text-sm text-blue-700 disabled:opacity-40"
      >
        + Add <span className="sr-only">{axisLabel.toLowerCase()}</span>
      </button>
      {open && (
        <ul
          id={listId}
          ref={listRef}
          role="listbox"
          tabIndex={0}
          aria-label={`Add ${axisLabel.toLowerCase()}`}
          aria-activedescendant={options[active] ? `${listId}-${options[active].key}` : undefined}
          onKeyDown={onKeyDown}
          onBlur={(e) => {
            if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setOpen(false)
          }}
          className="absolute z-10 mt-1 max-h-64 min-w-48 overflow-auto rounded-md border bg-white py-1 shadow-lg"
        >
          {options.length === 0 && <li className="px-3 py-2 text-sm text-gray-500">No more values</li>}
          {options.map((option, index) => (
            <li
              key={option.key}
              id={`${listId}-${option.key}`}
              role="option"
              aria-selected={index === active}
              title={option.definition}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => pick(option.key)}
              className={`cursor-pointer px-3 py-2 text-sm ${index === active ? "bg-blue-50" : ""}`}
            >
              {option.label}
            </li>
          ))}
        </ul>
      )}
    </span>
  )
}
