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

const mockClusterTwo: IngestionCluster = {
  members: [
    { image_id: 'pending-2', filename: 'second.jpg', status: 'pending', ocr_text: null },
  ],
  edges: [],
}

const mockClusterThree: IngestionCluster = {
  members: [
    { image_id: 'pending-3', filename: 'third.jpg', status: 'pending', ocr_text: null },
  ],
  edges: [],
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
    expect(screen.getByText('"nice meme bro"')).toBeInTheDocument()
    expect(screen.getByText('"original template text"')).toBeInTheDocument()
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

  describe('submit all decisions', () => {
    it('does not show "Submit all decisions" until at least one decision is made', async () => {
      const api = makeMockApi({
        getIngestionRunStatus: vi.fn().mockResolvedValue(mockStatus),
        getIngestionClusters: vi.fn().mockResolvedValue([mockCluster, mockClusterTwo]),
      })
      render(<IngestionReviewPage memesApi={api} />)

      await waitFor(() => expect(screen.getByText('new.jpg')).toBeInTheDocument())
      expect(screen.queryByText(/Submit all decisions/)).not.toBeInTheDocument()
    })

    it('shows the correct cluster/image counts across clusters with mixed decided and undecided members', async () => {
      const api = makeMockApi({
        getIngestionRunStatus: vi.fn().mockResolvedValue(mockStatus),
        getIngestionClusters: vi.fn().mockResolvedValue([mockCluster, mockClusterTwo]),
      })
      const user = userEvent.setup()
      render(<IngestionReviewPage memesApi={api} />)

      await waitFor(() => expect(screen.getByText('new.jpg')).toBeInTheDocument())

      // Decides pending-1 (in mockCluster) only -- mockClusterTwo's pending-2 stays undecided.
      await user.click(screen.getAllByText('Reject')[0])

      expect(screen.getByText('Submit all decisions (1 cluster, 1 image)')).toBeInTheDocument()
    })

    it('submits decisions for all clusters with a decision, after confirming, excluding undecided members and clusters entirely', async () => {
      const resolve = vi.fn().mockResolvedValue({ rejected: ['pending-1'], kept: ['pending-3'] })
      const api = makeMockApi({
        getIngestionRunStatus: vi.fn().mockResolvedValue(mockStatus),
        getIngestionClusters: vi.fn().mockResolvedValue([mockCluster, mockClusterTwo, mockClusterThree]),
        resolveIngestionCluster: resolve,
      })
      const user = userEvent.setup()
      render(<IngestionReviewPage memesApi={api} />)

      await waitFor(() => expect(screen.getByText('new.jpg')).toBeInTheDocument())

      // Keep buttons render in cluster order: pending-1 (index 0), pending-2 (index 1),
      // pending-3 (index 2) -- same for Reject. Decide pending-1 (reject) and pending-3 (keep);
      // leave mockClusterTwo's pending-2 undecided.
      await user.click(screen.getAllByText('Reject')[0])
      await user.click(screen.getAllByText('Keep')[2])

      expect(screen.getByText('Submit all decisions (2 clusters, 2 images)')).toBeInTheDocument()

      await user.click(screen.getByText(/Submit all decisions/))
      await user.click(screen.getByText('Confirm?'))

      await waitFor(() => expect(resolve).toHaveBeenCalledTimes(1))
      const [calledTier, calledDecisions] = resolve.mock.calls[0]
      expect(calledTier).toBe('tier_a')
      expect(calledDecisions).toHaveLength(2) // excludes pending-2 (undecided) entirely
      expect(calledDecisions).toEqual(expect.arrayContaining([
        { image_id: 'pending-1', decision: 'reject' },
        { image_id: 'pending-3', decision: 'keep' },
      ]))
    })

    it('disables per-cluster submit buttons while "submit all" is in flight', async () => {
      const resolve = vi.fn().mockImplementation(() => new Promise(() => {})) // never resolves
      const api = makeMockApi({
        getIngestionRunStatus: vi.fn().mockResolvedValue(mockStatus),
        getIngestionClusters: vi.fn().mockResolvedValue([mockCluster, mockClusterTwo]),
        resolveIngestionCluster: resolve,
      })
      const user = userEvent.setup()
      render(<IngestionReviewPage memesApi={api} />)

      await waitFor(() => expect(screen.getByText('new.jpg')).toBeInTheDocument())

      await user.click(screen.getAllByText('Reject')[0]) // decide pending-1 -- mockCluster's own
      // "Submit decisions" button would otherwise now be enabled.

      await user.click(screen.getByText(/Submit all decisions/))
      await user.click(screen.getByText('Confirm?'))

      await waitFor(() => expect(screen.getAllByText('Submit decisions')[0]).toBeDisabled())
    })
  })
})
