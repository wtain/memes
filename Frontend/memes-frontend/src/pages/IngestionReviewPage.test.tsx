import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import IngestionReviewPage from './IngestionReviewPage'
import { makeMockApi } from '../test/mockApi'
import type { IngestionCluster, IngestionRunStatus } from '../types/generated/all'

const mockStatus: IngestionRunStatus = {
  run_id: 'run-1',
  status: 'started',
  stage: 'tier_a_review',
  stats: { intake: 3, registered: 2 },
  created_at: '2026-07-25T00:00:00Z',
  completed_at: null,
}

const mockCluster: IngestionCluster = {
  members: [
    { image_id: 'pending-1', filename: 'new.jpg', status: 'pending', ocr_text: null },
    { image_id: 'active-1', filename: 'existing.jpg', status: 'active', ocr_text: 'existing meme text' },
  ],
  edges: [
    { image_id1: 'pending-1', image_id2: 'active-1', distance: 0.021, match_source: 'cross_corpus' },
  ],
}

describe('IngestionReviewPage', () => {
  it('shows a message when there is no active ingestion run', async () => {
    const api = makeMockApi()
    render(<IngestionReviewPage memesApi={api} />)

    await waitFor(() =>
      expect(screen.getByText('No ingestion run is currently in progress.')).toBeInTheDocument()
    )
  })

  it('renders cluster members and the run stage once loaded', async () => {
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue(mockStatus),
      getIngestionClusters: vi.fn().mockResolvedValue([mockCluster]),
    })
    render(<IngestionReviewPage memesApi={api} />)

    await waitFor(() => expect(screen.getByText('new.jpg')).toBeInTheDocument())
    expect(screen.getByText('existing.jpg')).toBeInTheDocument()
    expect(screen.getByText('tier_a_review')).toBeInTheDocument()
    // active member is read-only context -- no Keep/Reject buttons for it
    expect(screen.getAllByText('Keep')).toHaveLength(1)
  })

  it('submits a reject decision for the pending member and reloads', async () => {
    const resolve = vi.fn().mockResolvedValue({ rejected: ['pending-1'], kept: [] })
    const getIngestionClusters = vi.fn().mockResolvedValue([mockCluster])
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue(mockStatus),
      getIngestionClusters,
      resolveIngestionCluster: resolve,
    })
    const user = userEvent.setup()
    render(<IngestionReviewPage memesApi={api} />)

    await waitFor(() => expect(screen.getByText('new.jpg')).toBeInTheDocument())

    await user.click(screen.getByText('Reject'))
    await user.click(screen.getByText('Submit decisions'))

    await waitFor(() =>
      expect(resolve).toHaveBeenCalledWith('tier_a', [{ image_id: 'pending-1', decision: 'reject' }])
    )
    expect(getIngestionClusters).toHaveBeenCalledTimes(2) // initial load + reload after submit
  })

  it('disables submit until a decision is made', async () => {
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue(mockStatus),
      getIngestionClusters: vi.fn().mockResolvedValue([mockCluster]),
    })
    render(<IngestionReviewPage memesApi={api} />)

    await waitFor(() => expect(screen.getByText('Submit decisions')).toBeInTheDocument())
    expect(screen.getByText('Submit decisions')).toBeDisabled()
  })

  it('switches to the Tier B queue and shows OCR text once the run reaches tier_b_review', async () => {
    const tierBCluster: IngestionCluster = {
      members: [
        { image_id: 'pending-2', filename: 'meme.jpg', status: 'pending', ocr_text: 'nice meme bro' },
        { image_id: 'active-2', filename: 'template.jpg', status: 'active', ocr_text: 'original template text' },
      ],
      edges: [
        { image_id1: 'pending-2', image_id2: 'active-2', distance: 0.12, match_source: 'cross_corpus' },
      ],
    }
    const getIngestionClusters = vi.fn().mockResolvedValue([tierBCluster])
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue({ ...mockStatus, stage: 'tier_b_review' }),
      getIngestionClusters,
    })
    render(<IngestionReviewPage memesApi={api} />)

    await waitFor(() => expect(screen.getByText('Ingestion Review — Tier B')).toBeInTheDocument())
    expect(getIngestionClusters).toHaveBeenCalledWith('tier_b')
    expect(screen.getByText('“nice meme bro”')).toBeInTheDocument()
    expect(screen.getByText('“original template text”')).toBeInTheDocument()
  })

  it('opens a lightbox with the full image when a thumbnail is clicked, and closes it', async () => {
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue(mockStatus),
      getIngestionClusters: vi.fn().mockResolvedValue([mockCluster]),
    })
    const user = userEvent.setup()
    render(<IngestionReviewPage memesApi={api} />)

    await waitFor(() => expect(screen.getByText('new.jpg')).toBeInTheDocument())

    // Only the thumbnail exists before the lightbox is opened.
    expect(screen.getAllByAltText('new.jpg')).toHaveLength(1)

    await user.click(screen.getAllByAltText('new.jpg')[0])

    // Opening it adds a second image with the same alt text -- the enlarged one.
    const images = await waitFor(() => {
      const found = screen.getAllByAltText('new.jpg')
      expect(found).toHaveLength(2)
      return found
    })
    const enlargedImage = images[1]
    expect(enlargedImage).toHaveAttribute('src', api.getImageUrlById('pending-1'))

    await user.click(screen.getByText('✕'))

    await waitFor(() => expect(screen.getAllByAltText('new.jpg')).toHaveLength(1))
  })

  it('shows a waiting message during the OCR pre-pass stage, without fetching clusters', async () => {
    const getIngestionClusters = vi.fn()
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue({ ...mockStatus, stage: 'ocr_prepass' }),
      getIngestionClusters,
    })
    render(<IngestionReviewPage memesApi={api} />)

    await waitFor(() =>
      expect(screen.getByText(/OCR is running/)).toBeInTheDocument()
    )
    expect(getIngestionClusters).not.toHaveBeenCalled()
  })
})
