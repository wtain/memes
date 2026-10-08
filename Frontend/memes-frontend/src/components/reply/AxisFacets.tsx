import { labelFor } from "../../reply/taxonomy"
import type { Facet, Filters, TaxonomyResponse } from "../../reply/types"
import { AXES, AXIS_LABELS } from "../../reply/types"
import { hasFilters } from "../../reply/filters"

type Props = {
  facets: Facet[]
  selected: Filters
  taxonomy: TaxonomyResponse | null
  onChange: (axis: (typeof AXES)[number], values: string[]) => void
  onClear: () => void
}

export default function AxisFacets({ facets, selected, taxonomy, onChange, onClear }: Props) {
  return (
    <div>
      {hasFilters(selected) && (
        <button type="button" onClick={onClear} className="mb-3 min-h-11 text-sm text-blue-600 hover:underline">
          Clear filters
        </button>
      )}
      {AXES.map((axis) => {
        const buckets = facets.find((facet) => facet.name === axis)?.buckets ?? []
        const counts = new Map(buckets.map((bucket) => [bucket.value, bucket.count]))
        // A selected value that the filtered results no longer contain stays visible (count 0) so it can be unchecked.
        for (const value of selected[axis]) if (!counts.has(value)) counts.set(value, 0)
        const rows = [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
        if (rows.length === 0) return null
        return (
          <fieldset key={axis} className="mb-4">
            <legend className="mb-1 font-medium">{AXIS_LABELS[axis]}</legend>
            {rows.map(([value, count]) => {
              const checked = selected[axis].includes(value)
              return (
                <label key={value} className="flex min-h-11 items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() =>
                      onChange(axis, checked ? selected[axis].filter((v) => v !== value) : [...selected[axis], value])
                    }
                  />
                  <span>{`${labelFor(taxonomy, axis, value)} (${count})`}</span>
                </label>
              )
            })}
          </fieldset>
        )
      })}
    </div>
  )
}
