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
    { image_id: 'pending-1', filename: 'new.jpg', status: 'pending' },
    { image_id: 'active-1', filename: 'existing.jpg', status: 'active' },
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
})
