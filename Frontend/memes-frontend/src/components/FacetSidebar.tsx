import { useState } from "react"
import { Facet } from "../types/facet"
import { MultiSelectFacet } from "./MultiSelectFacet"

type Props = {
  facets: Facet[]
  selected: Record<string, string[]>
  onFilterChange: (facet: string, values: string[]) => void
  onClear: () => void
}

export default function FacetSidebar({ facets, selected, onFilterChange, onClear }: Props) {
  const hasSelection = Object.values(selected).some(v => v.length > 0)
  const [mobileOpen, setMobileOpen] = useState(false)

  const activeFilterCount = Object.values(selected).reduce((sum, v) => sum + v.length, 0)

  const sidebarContent = (
    <>
      {hasSelection && (
        <button
          onClick={onClear}
          className="mb-4 text-sm text-blue-600 hover:underline"
        >
          Clear filters
        </button>
      )}

      {facets.map((facet) => {
        const selectedValues = selected[facet.name] ?? []

        return (
          <div key={facet.name} className="mb-6">
            <MultiSelectFacet
              label={facet.name}
              selected={selectedValues}
              onChange={(nextValues) => onFilterChange(facet.name, nextValues)}
              loadOptions={(q) =>
                Promise.resolve(
                  facet.buckets
                    .filter(b => b.value.toLowerCase().includes(q.toLowerCase()))
                    .map(b => ({ value: b.value, label: `${b.value} (${b.count})` }))
                )
              }
            />
          </div>
        )
      })}
    </>
  )

  return (
    <>
      {/* Mobile toggle button — only visible on small screens */}
      <div className="md:hidden mb-3">
        <button
          onClick={() => setMobileOpen(true)}
          className="flex items-center gap-2 px-3 py-1.5 text-sm border rounded-md bg-white shadow-sm hover:bg-gray-50"
        >
          <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 4h18M6 8h12M9 12h6" />
          </svg>
          Filters
          {activeFilterCount > 0 && (
            <span className="ml-1 px-1.5 py-0.5 text-xs bg-blue-600 text-white rounded-full">
              {activeFilterCount}
            </span>
          )}
        </button>
      </div>

      {/* Mobile drawer overlay */}
      {mobileOpen && (
        <div className="md:hidden fixed inset-0 z-50 flex">
          {/* Backdrop */}
          <div
            className="absolute inset-0 bg-black/40"
            onClick={() => setMobileOpen(false)}
          />

          {/* Drawer panel */}
          <aside className="relative z-10 w-72 max-w-[85vw] h-full bg-white shadow-xl flex flex-col">
            <div className="flex items-center justify-between px-4 py-3 border-b">
              <span className="font-medium text-sm">Filters</span>
              <button
                onClick={() => setMobileOpen(false)}
                className="p-1 rounded hover:bg-gray-100"
                aria-label="Close filters"
              >
                <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            <div className="overflow-y-auto flex-1 px-4 py-4">
              {sidebarContent}
            </div>
          </aside>
        </div>
      )}

      {/* Desktop sidebar — hidden on mobile */}
      <aside className="hidden md:block w-64 shrink-0 border-r pr-4">
        {sidebarContent}
      </aside>
    </>
  )
}