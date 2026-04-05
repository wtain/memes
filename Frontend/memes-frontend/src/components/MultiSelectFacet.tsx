import { useEffect, useRef, useState } from "react"

type Option = {
  value: string
  label: string
}

type Props = {
  label: string
  selected: string[]
  onChange: (values: string[]) => void
  loadOptions: (query: string) => Promise<Option[]>
}

export function MultiSelectFacet({
  label,
  selected,
  onChange,
  loadOptions,
}: Props) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")
  const [options, setOptions] = useState<Option[]>([])

  const ref = useRef<HTMLDivElement>(null)

  // Load options
  useEffect(() => {
    loadOptions(query).then(setOptions)
  }, [query])

  // Close on outside click
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener("mousedown", handleClick)
    return () => document.removeEventListener("mousedown", handleClick)
  }, [])

  function toggle(value: string) {
    if (selected.includes(value)) {
      onChange(selected.filter(v => v !== value))
    } else {
      onChange([...selected, value])
    }
  }

  function displayValue() {
    if (selected.length === 0) return "(none)"
    if (selected.length === 1) {
      return options.find(o => o.value === selected[0])?.label ?? selected[0]
    }
    return "(many)"
  }

  return (
    <div ref={ref} className="relative w-full">
        {/* Label */}
        <div className="text-xs mb-1 capitalize text-gray-500">{label}</div>

        {/* Control */}
        <div
            className="border rounded px-2 py-1 bg-white cursor-pointer text-sm flex items-center justify-between gap-2"
            onClick={() => setOpen(o => !o)}
        >
            <span className="truncate">{displayValue()}</span>
            <div className="flex items-center gap-1 shrink-0">
                {selected.length > 0 && (
                    <button
                        onClick={e => { e.stopPropagation(); onChange([]) }}
                        className="text-gray-300 hover:text-gray-500 transition-colors"
                        title="Clear selection"
                    >
                        <svg className="w-3.5 h-3.5" viewBox="0 0 20 20" fill="currentColor">
                            <path d="M6.28 5.22a.75.75 0 00-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 101.06 1.06L10 11.06l3.72 3.72a.75.75 0 101.06-1.06L11.06 10l3.72-3.72a.75.75 0 00-1.06-1.06L10 8.94 6.28 5.22z" />
                        </svg>
                    </button>
                )}
                <svg
                    className={`w-4 h-4 text-gray-400 transition-transform duration-200 ${open ? "rotate-180" : ""}`}
                    viewBox="0 0 20 20"
                    fill="currentColor"
                >
                    <path
                        fillRule="evenodd"
                        d="M5.23 7.21a.75.75 0 011.06.02L10 11.168l3.71-3.938a.75.75 0 111.08 1.04l-4.25 4.5a.75.75 0 01-1.08 0l-4.25-4.5a.75.75 0 01.02-1.06z"
                        clipRule="evenodd"
                    />
                </svg>
            </div>
        </div>

        {/* Dropdown */}
        {open && (
            <div className="absolute z-50 mt-1 w-full bg-white border rounded shadow-lg">
            {/* Search */}
            <input
                className="w-full px-2 py-1 border-b outline-none"
                placeholder="Filter..."
                value={query}
                onChange={e => setQuery(e.target.value)}
            />

            {/* Options */}
            <div className="max-h-60 overflow-y-auto">
                {options.map(option => (
                <label
                    key={option.value}
                    className="flex items-center px-2 py-1 hover:bg-gray-100 cursor-pointer"
                >
                    <input
                    type="checkbox"
                    checked={selected.includes(option.value)}
                    onChange={() => toggle(option.value)}
                    className="mr-2"
                    />
                    {option.label}
                </label>
                ))}

                {options.length === 0 && (
                <div className="p-2 text-sm text-gray-500">
                    No results
                </div>
                )}
            </div>
        </div>
      )}
    </div>
  )
}