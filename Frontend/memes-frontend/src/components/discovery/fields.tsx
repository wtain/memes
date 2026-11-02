const LABEL = "flex min-h-11 items-center gap-2 text-sm"
const INPUT = "min-h-11 rounded border px-2"

export function NumberField({ label, value, onChange, min }: {
  label: string; value: number | undefined; onChange: (value: number | undefined) => void; min?: number
}) {
  return (
    <label className={LABEL}>
      <span>{label}</span>
      <input
        type="number" min={min} value={value ?? ""} className={`${INPUT} w-24`}
        onChange={(event) => {
          const text = event.target.value
          onChange(text === "" ? undefined : Number(text))
        }}
      />
    </label>
  )
}

export function SelectField<T extends string | number>({ label, value, options, onChange }: {
  label: string; value: T; options: readonly T[]; onChange: (value: T) => void
}) {
  return (
    <label className={LABEL}>
      <span>{label}</span>
      <select
        value={String(value)} className={INPUT}
        onChange={(event) => {
          const chosen = options.find((option) => String(option) === event.target.value)
          if (chosen !== undefined) onChange(chosen)
        }}
      >
        {options.map((option) => <option key={String(option)} value={String(option)}>{String(option)}</option>)}
      </select>
    </label>
  )
}

export function CheckField({ label, checked, onChange }: {
  label: string; checked: boolean; onChange: (checked: boolean) => void
}) {
  return (
    <label className={LABEL}>
      <input type="checkbox" checked={checked} className="h-5 w-5" onChange={(event) => onChange(event.target.checked)} />
      <span>{label}</span>
    </label>
  )
}

export const SIZES = [50, 100, 200] as const
