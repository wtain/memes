import { render, screen, fireEvent } from '@testing-library/react'
import FacetSidebar from './FacetSidebar'
import type { Facet } from '../types/generated/all'

const facets: Facet[] = [
  {
    name: 'category',
    buckets: [
      { value: 'cats', count: 10 },
      { value: 'dogs', count: 5 },
    ],
  },
]

describe('FacetSidebar', () => {
  it('does not show the clear button when no filters are selected', () => {
    render(
      <FacetSidebar facets={facets} selected={{}} onFilterChange={() => {}} onClear={() => {}} />
    )
    expect(screen.queryByText('Clear filters')).not.toBeInTheDocument()
  })

  it('shows the clear button in the desktop sidebar when filters are selected', () => {
    render(
      <FacetSidebar
        facets={facets}
        selected={{ category: ['cats'] }}
        onFilterChange={() => {}}
        onClear={() => {}}
      />
    )
    expect(screen.getByText('Clear filters')).toBeInTheDocument()
  })

  it('calls onClear when the clear button is clicked', () => {
    const onClear = vi.fn()
    render(
      <FacetSidebar
        facets={facets}
        selected={{ category: ['cats'] }}
        onFilterChange={() => {}}
        onClear={onClear}
      />
    )
    fireEvent.click(screen.getAllByText('Clear filters')[0])
    expect(onClear).toHaveBeenCalledOnce()
  })

  it('shows active filter count badge on the mobile toggle button', () => {
    render(
      <FacetSidebar
        facets={facets}
        selected={{ category: ['cats', 'dogs'] }}
        onFilterChange={() => {}}
        onClear={() => {}}
      />
    )
    expect(screen.getByText('2')).toBeInTheDocument()
  })

  it('opens the mobile drawer when the Filters button is clicked', () => {
    render(
      <FacetSidebar facets={facets} selected={{}} onFilterChange={() => {}} onClear={() => {}} />
    )
    fireEvent.click(screen.getByRole('button', { name: /Filters/ }))
    expect(screen.getByLabelText('Close filters')).toBeInTheDocument()
  })

  it('closes the mobile drawer when the close button is clicked', () => {
    render(
      <FacetSidebar facets={facets} selected={{}} onFilterChange={() => {}} onClear={() => {}} />
    )
    fireEvent.click(screen.getByRole('button', { name: /Filters/ }))
    fireEvent.click(screen.getByLabelText('Close filters'))
    expect(screen.queryByLabelText('Close filters')).not.toBeInTheDocument()
  })

  it('closes the mobile drawer when backdrop is clicked', () => {
    render(
      <FacetSidebar facets={facets} selected={{}} onFilterChange={() => {}} onClear={() => {}} />
    )
    fireEvent.click(screen.getByRole('button', { name: /Filters/ }))
    const backdrop = document.querySelector('.bg-black\\/40')!
    fireEvent.click(backdrop)
    expect(screen.queryByLabelText('Close filters')).not.toBeInTheDocument()
  })
})
