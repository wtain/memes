import { Facet } from "../types/facet"

type Props = {
  facets: Facet[]
  selected: Record<string, string[]>
  onToggle: (facet: string, value: string) => void
}

export default function FacetSidebar({ facets, selected, onToggle }: Props) {
  return (
    <aside className="w-64 shrink-0 border-r pr-4">
      {facets.map((facet) => (
        <div key={facet.name} className="mb-6">
          <h3 className="font-semibold mb-2 capitalize">
            {facet.name}
          </h3>

          <ul className="space-y-1">
            {facet.buckets.map((bucket) => {
              const checked =
                selected[facet.name]?.includes(bucket.value) ?? false

              return (
                <li key={bucket.value}>
                  <label className="flex items-center gap-2 text-sm cursor-pointer">
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() =>
                        onToggle(facet.name, bucket.value)
                      }
                    />
                    <span>
                      {bucket.value}
                      <span className="text-gray-400 ml-1">
                        ({bucket.count})
                      </span>
                    </span>
                  </label>
                </li>
              )
            })}
          </ul>
        </div>
      ))}
    </aside>
  )
}
