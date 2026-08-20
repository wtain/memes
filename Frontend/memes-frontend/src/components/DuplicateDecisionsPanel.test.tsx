import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import DuplicateDecisionsPanel from './DuplicateDecisionsPanel'
import { makeMockApi } from '../test/mockApi'

describe('DuplicateDecisionsPanel', () => {
  it('lists decisions and undoes one on click', async () => {
    const api = makeMockApi({
      listDuplicateDecisions: vi.fn().mockResolvedValue({
        items: [{
          image_id1: 'a', filename1: 'a.jpg',
          image_id2: 'b', filename2: 'b.jpg',
          decided_at: '2026-08-19T00:00:00Z',
        }],
        total: 1,
      }),
      undoDismissDuplicates: vi.fn().mockResolvedValue(undefined),
    })
    render(<DuplicateDecisionsPanel memesApi={api} />)

    expect(await screen.findByText('a.jpg')).toBeInTheDocument()
    expect(screen.getByAltText('a.jpg')).toHaveAttribute('src', api.getImageUrlById('a'))
    expect(screen.getByAltText('b.jpg')).toHaveAttribute('src', api.getImageUrlById('b'))

    fireEvent.click(screen.getByRole('button', { name: 'Undo' }))

    await waitFor(() => {
      expect(api.undoDismissDuplicates).toHaveBeenCalledWith([{ image_id1: 'a', image_id2: 'b' }])
    })
  })

  it('shows an empty state with no decisions', async () => {
    const api = makeMockApi({
      listDuplicateDecisions: vi.fn().mockResolvedValue({ items: [], total: 0 }),
    })
    render(<DuplicateDecisionsPanel memesApi={api} />)

    expect(await screen.findByText('No decisions yet.')).toBeInTheDocument()
  })
})
