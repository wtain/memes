import { useRef } from "react"
import { labelFor, maxValuesFor, optionsFor } from "../../reply/taxonomy"
import type { AxisName, QueryProfile, TaxonomyResponse } from "../../reply/types"
import { AXES, AXIS_LABELS, SITUATION_LIMIT } from "../../reply/types"
import { hasAnyValue } from "../../reply/profile"
import ChipGroup from "./ChipGroup"

type Props = {
  profile: QueryProfile
  edited: boolean
  taxonomy: TaxonomyResponse | null
  onSituation: (text: string) => void
  onAdd: (axis: AxisName, key: string) => void
  onRemove: (axis: AxisName, key: string) => void
  onReset: () => void
}

export default function ReadingPanel({ profile, edited, taxonomy, onSituation, onAdd, onRemove, onReset }: Props) {
  const situationRef = useRef<HTMLInputElement>(null)
  const unreadable = profile.degraded && !hasAnyValue(profile)

  return (
    <section aria-labelledby="reading-heading" className="rounded-xl bg-white p-4 shadow-sm">
      <div className="mb-2 flex flex-wrap items-center gap-3">
        <h2 id="reading-heading" className="text-lg font-semibold">How I read your message</h2>
        <span className="text-sm text-gray-500">Language: {profile.language}</span>
        {edited && <span className="rounded bg-yellow-100 px-2 py-0.5 text-sm">Edited</span>}
        {edited && (
          <button
            type="button"
            onClick={() => {
              // This button unmounts once the edit is gone: keep focus inside the panel.
              situationRef.current?.focus()
              onReset()
            }}
            className="min-h-11 text-sm text-blue-600 hover:underline">
            Reset to original reading
          </button>
        )}
      </div>

      {unreadable && (
        <p className="mb-3 text-sm text-gray-600">
          We couldn't read your message structurally. Describe the situation below and add values yourself to refine the search.
        </p>
      )}

      <label htmlFor="reading-situation" className="mb-1 block text-sm font-medium text-gray-700">Situation</label>
      <input
        ref={situationRef}
        id="reading-situation"
        value={profile.situation ?? ""}
        maxLength={SITUATION_LIMIT}
        onChange={(e) => onSituation(e.target.value)}
        className="mb-4 w-full rounded-md border px-3 py-2"
      />

      {AXES.map((axis) => (
        <ChipGroup
          key={axis}
          axis={axis}
          label={AXIS_LABELS[axis]}
          values={profile[axis]}
          labelOf={(key) => labelFor(taxonomy, axis, key)}
          options={optionsFor(taxonomy, axis)}
          maxValues={maxValuesFor(taxonomy, axis)}
          pickerDisabled={taxonomy === null}
          onAdd={(key) => onAdd(axis, key)}
          onRemove={(key) => onRemove(axis, key)}
        />
      ))}
    </section>
  )
}
