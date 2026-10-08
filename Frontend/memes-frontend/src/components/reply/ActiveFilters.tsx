import { labelFor } from "../../reply/taxonomy"
import type { AxisName, Filters, TaxonomyResponse } from "../../reply/types"
import { AXES } from "../../reply/types"

type Props = {
  filters: Filters
  taxonomy: TaxonomyResponse | null
  onRemove: (axis: AxisName, value: string) => void
}

export default function ActiveFilters({ filters, taxonomy, onRemove }: Props) {
  const entries = AXES.flatMap((axis) => filters[axis].map((value) => ({ axis, value })))
  if (entries.length === 0) return null
  return (
    <ul aria-label="Active filters" className="mb-3 flex flex-wrap gap-2">
      {entries.map(({ axis, value }) => {
        const label = labelFor(taxonomy, axis, value)
        return (
          <li key={`${axis}:${value}`} className="flex items-center gap-1 rounded-full bg-gray-200 py-1 pl-3 pr-1 text-sm">
            <span>{label}</span>
            <button
              type="button"
              aria-label={`Remove filter ${axis}: ${label}`}
              onClick={() => onRemove(axis, value)}
              className="flex h-11 w-11 items-center justify-center rounded-full hover:bg-gray-300"
            >
              <span aria-hidden="true">×</span>
            </button>
          </li>
        )
      })}
    </ul>
  )
}
