import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MultiSelectFacet } from './MultiSelectFacet'

const allOptions = [
  { value: 'cats', label: 'cats (12)' },
  { value: 'dogs', label: 'dogs (8)' },
  { value: 'funny', label: 'funny (25)' },
]

function makeLoadOptions() {
  return vi.fn((q: string) =>
    Promise.resolve(allOptions.filter(o => o.value.includes(q.toLowerCase())))
  )
}

describe('MultiSelectFacet', () => {
  it('shows (none) when nothing is selected', async () => {
    render(
      <MultiSelectFacet label="Category" selected={[]} onChange={() => {}} loadOptions={makeLoadOptions()} />
    )
    expect(screen.getByText('(none)')).toBeInTheDocument()
  })

  it('shows the label of the single selected option', async () => {
    render(
      <MultiSelectFacet
        label="Category"
        selected={['cats']}
        onChange={() => {}}
        loadOptions={makeLoadOptions()}
      />
    )
    await waitFor(() => expect(screen.getByText('cats (12)')).toBeInTheDocument())
  })

  it('shows (many) when multiple options are selected', () => {
    render(
      <MultiSelectFacet
        label="Category"
        selected={['cats', 'dogs']}
        onChange={() => {}}
        loadOptions={makeLoadOptions()}
      />
    )
    expect(screen.getByText('(many)')).toBeInTheDocument()
  })

  it('opens dropdown and shows options on control click', async () => {
    render(
      <MultiSelectFacet label="Category" selected={[]} onChange={() => {}} loadOptions={makeLoadOptions()} />
    )
    fireEvent.click(screen.getByText('(none)').parentElement!)
    await waitFor(() => {
      expect(screen.getByPlaceholderText('Filter...')).toBeInTheDocument()
      expect(screen.getByText('cats (12)')).toBeInTheDocument()
    })
  })

  it('calls onChange with the new value when an option is toggled on', async () => {
    const onChange = vi.fn()
    render(
      <MultiSelectFacet label="Category" selected={[]} onChange={onChange} loadOptions={makeLoadOptions()} />
    )
    fireEvent.click(screen.getByText('(none)').parentElement!)
    await waitFor(() => screen.getByText('cats (12)'))
    fireEvent.click(screen.getAllByRole('checkbox')[0])
    expect(onChange).toHaveBeenCalledWith(['cats'])
  })

  it('calls onChange removing the value when an option is toggled off', async () => {
    const onChange = vi.fn()
    render(
      <MultiSelectFacet
        label="Category"
        selected={['cats']}
        onChange={onChange}
        loadOptions={makeLoadOptions()}
      />
    )
    // Wait for options to load so the control shows the label 'cats (12)'
    const controlSpan = await screen.findByText('cats (12)')
    // Open the dropdown by clicking the control div
    fireEvent.click(controlSpan.parentElement!)
    await waitFor(() => screen.getByPlaceholderText('Filter...'))
    // The cats checkbox is checked because 'cats' is in selected
    fireEvent.click(screen.getByRole('checkbox', { name: 'cats (12)' }))
    expect(onChange).toHaveBeenCalledWith([])
  })

  it('calls onChange with empty array when clear button is clicked', async () => {
    const onChange = vi.fn()
    render(
      <MultiSelectFacet
        label="Category"
        selected={['cats']}
        onChange={onChange}
        loadOptions={makeLoadOptions()}
      />
    )
    await waitFor(() => screen.getByTitle('Clear selection'))
    fireEvent.click(screen.getByTitle('Clear selection'))
    expect(onChange).toHaveBeenCalledWith([])
  })

  it('shows "No results" when filter query matches nothing', async () => {
    render(
      <MultiSelectFacet label="Category" selected={[]} onChange={() => {}} loadOptions={makeLoadOptions()} />
    )
    fireEvent.click(screen.getByText('(none)').parentElement!)
    await waitFor(() => screen.getByPlaceholderText('Filter...'))
    fireEvent.change(screen.getByPlaceholderText('Filter...'), { target: { value: 'zzz' } })
    await waitFor(() => expect(screen.getByText('No results')).toBeInTheDocument())
  })
})
