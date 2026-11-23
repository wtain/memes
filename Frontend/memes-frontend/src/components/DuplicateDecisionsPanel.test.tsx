import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import DuplicateDecisionsPanel from './DuplicateDecisionsPanel'
import { makeMockApi } from '../test/mockApi'
import { truncateMiddle } from '../utils/truncateMiddle'

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

  it('shortens very long file names and keeps the full name in a tooltip', async () => {
    const long1 = 'IMG_20260101_123456_' + 'very_long_descriptive_part_'.repeat(6) + 'final.jpg'
    const long2 = 'x'.repeat(200) + '.png'
    const api = makeMockApi({
      listDuplicateDecisions: vi.fn().mockResolvedValue({
        items: [{
          image_id1: 'a', filename1: long1,
          image_id2: 'b', filename2: long2,
          decided_at: '2026-08-19T00:00:00Z',
        }],
        total: 1,
      }),
    })
    render(<DuplicateDecisionsPanel memesApi={api} />)

    const first = await screen.findByTitle(long1)
    expect(first.textContent).toBe(truncateMiddle(long1))
    expect(first.textContent).toContain('…')
    expect(first.textContent!.endsWith('.jpg')).toBe(true)
    const second = screen.getByTitle(long2)
    expect(second.textContent).toBe(truncateMiddle(long2))
    // the thumbnails keep the full name as their accessible name
    expect(screen.getByAltText(long1)).toBeInTheDocument()
    expect(screen.queryByText(long1)).not.toBeInTheDocument()
  })

  it('gives a short file name no tooltip', async () => {
    const api = makeMockApi({
      listDuplicateDecisions: vi.fn().mockResolvedValue({
        items: [{
          image_id1: 'a', filename1: 'a.jpg',
          image_id2: 'b', filename2: 'b.jpg',
          decided_at: '2026-08-19T00:00:00Z',
        }],
        total: 1,
      }),
    })
    render(<DuplicateDecisionsPanel memesApi={api} />)

    expect(await screen.findByText('a.jpg')).not.toHaveAttribute('title')
  })

  it('shows an empty state with no decisions', async () => {
    const api = makeMockApi({
      listDuplicateDecisions: vi.fn().mockResolvedValue({ items: [], total: 0 }),
    })
    render(<DuplicateDecisionsPanel memesApi={api} />)

    expect(await screen.findByText('No decisions yet.')).toBeInTheDocument()
  })
})
