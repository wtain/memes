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
    const resolve = vi.fn().mockResolvedValue({ rejected: ['pending-1'], kept: [], failed: [], move_failed: [] })
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

  it('clears local decisions when the review tier changes', async () => {
    // Regression test: a decision set but never submitted must not survive into a later tier's
    // review, even if the same image_id reappears there in a new cluster -- see
    // docs/superpowers/specs/2026-08-16-ingestion-decision-staleness-guard-design.md.
    const getIngestionRunStatus = vi.fn()
      .mockResolvedValueOnce(mockStatus) // tier_a_review
      .mockResolvedValueOnce({ ...mockStatus, stage: 'tier_b_review' }) // advances after submit's reload
    const tierBClusterReusingSameId: IngestionCluster = {
      members: [
        { image_id: 'pending-2', filename: 'second.jpg', status: 'pending', ocr_text: null },
      ],
      edges: [],
    }
    const resolve = vi.fn().mockResolvedValue({ rejected: ['pending-1'], kept: [], failed: [], move_failed: [] })
    const getIngestionClusters = vi.fn()
      .mockResolvedValueOnce([mockCluster, mockClusterTwo]) // initial tier_a load
      .mockResolvedValueOnce([tierBClusterReusingSameId]) // reload after submit, now tier_b
    const api = makeMockApi({
      getIngestionRunStatus, getIngestionClusters, resolveIngestionCluster: resolve,
    })
    const user = userEvent.setup()
    render(<IngestionReviewPage memesApi={api} />)

    await waitFor(() => expect(screen.getByText('new.jpg')).toBeInTheDocument())
    // Decide pending-1 (mockCluster, will be submitted) and pending-2 (mockClusterTwo, left
    // un-submitted -- this is the stale decision that must not survive the tier flip triggered
    // by pending-1's submit below).
    await user.click(screen.getAllByText('Reject')[0])
    await user.click(screen.getAllByText('Reject')[1])
    await user.click(screen.getAllByText('Submit decisions')[0]) // mockCluster's own submit

    await waitFor(() => expect(resolve).toHaveBeenCalledTimes(1))
    expect(resolve).toHaveBeenCalledWith('tier_a', [{ image_id: 'pending-1', decision: 'reject' }])

    // Reload landed on tier_b, reusing pending-2's id in a new cluster.
    await waitFor(() => expect(screen.getByText('Ingestion Review — Tier B')).toBeInTheDocument())
    expect(screen.getByText('second.jpg')).toBeInTheDocument()
    // pending-2's stale tier_a Reject must not have survived the tier change.
    expect(screen.getByText('Submit decisions')).toBeDisabled()
    expect(screen.getByText('Reject')).not.toHaveClass('bg-red-600')
  })

  it('prunes a silently-skipped decision so it does not resurrect if its member becomes pending again', async () => {
    // Regression test: resolve() can silently skip a decision (present in none of
    // rejected/kept/failed/move_failed) when its target is no longer pending at submit time
    // (e.g. reject_image's own pending guard, see
    // docs/superpowers/specs/2026-08-16-ingestion-decision-staleness-guard-design.md).
    // Clearing-by-response alone leaves that decision stuck in local state forever; if the
    // member's status later returns to "pending" (e.g. undone elsewhere), the stale decision
    // must not resurface as if the reviewer just decided it there. See
    // docs/superpowers/specs/2026-08-16-ingestion-resolve-atomicity-design.md.
    const activeOneCluster: IngestionCluster = {
      members: [{ image_id: 'pending-1', filename: 'new.jpg', status: 'active', ocr_text: null }],
      edges: [],
    }
    const resolve = vi.fn()
      .mockResolvedValueOnce({ rejected: ['pending-2'], kept: [], failed: [], move_failed: [] })
      .mockResolvedValueOnce({ rejected: ['pending-3'], kept: [], failed: [], move_failed: [] })
    const getIngestionClusters = vi.fn()
      .mockResolvedValueOnce([mockCluster, mockClusterTwo]) // pending-1, pending-2 both pending
      .mockResolvedValueOnce([activeOneCluster, mockClusterThree]) // pending-1 silently skipped, now active; pending-3 pending
      .mockResolvedValueOnce([mockCluster]) // pending-1 pending again
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue(mockStatus),
      getIngestionClusters,
      resolveIngestionCluster: resolve,
    })
    const user = userEvent.setup()
    render(<IngestionReviewPage memesApi={api} />)

    await waitFor(() => expect(screen.getByText('new.jpg')).toBeInTheDocument())
    // Decide pending-1 (will be silently skipped by the backend) and pending-2 (submitted normally).
    await user.click(screen.getAllByText('Reject')[0])
    await user.click(screen.getAllByText('Reject')[1])
    await user.click(screen.getAllByText('Submit decisions')[1]) // mockClusterTwo's own submit

    await waitFor(() => expect(resolve).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(screen.getByText('third.jpg')).toBeInTheDocument())

    // Decide and submit pending-3 to drive a second reload. Two clusters are on screen here
    // (activeOneCluster and mockClusterThree), so "Submit decisions" is ambiguous by plain
    // getByText -- disambiguate to mockClusterThree's own button (index 1), same pattern used
    // above for mockClusterTwo's submit.
    await user.click(screen.getByText('Reject'))
    await user.click(screen.getAllByText('Submit decisions')[1])

    await waitFor(() => expect(resolve).toHaveBeenCalledTimes(2))
    // pending-1 reappears as pending again. If its stale decision from the first round had
    // survived (never pruned, only ever hidden while non-pending), it would show as
    // already-decided here.
    await waitFor(() => expect(screen.getByText('new.jpg')).toBeInTheDocument())
    expect(screen.getByText('Submit decisions')).toBeDisabled()
    expect(screen.getByText('Reject')).not.toHaveClass('bg-red-600')
  })

  it('shows submit failures inline rather than replacing the whole page', async () => {
    const resolve = vi.fn().mockRejectedValue(new Error('network down'))
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue(mockStatus),
      getIngestionClusters: vi.fn().mockResolvedValue([mockCluster]),
      resolveIngestionCluster: resolve,
    })
    const user = userEvent.setup()
    render(<IngestionReviewPage memesApi={api} />)

    await waitFor(() => expect(screen.getByText('new.jpg')).toBeInTheDocument())
    await user.click(screen.getByText('Reject'))
    await user.click(screen.getByText('Submit decisions'))

    await waitFor(() => expect(screen.getByText('network down')).toBeInTheDocument())
    // A submit failure must not replace the whole page -- the cluster and its decision stay visible.
    expect(screen.getByText('new.jpg')).toBeInTheDocument()
    expect(screen.getByText('Reject')).toHaveClass('bg-red-600')
  })

  it('excludes a decided member from submission once its status is no longer pending', async () => {
    // Regression test: a decision set while a member was pending must not be submitted if a
    // reload shows it's since been resolved by a concurrent reviewer in the same tier (no tier
    // change involved -- that case is covered by Task 2's test) -- see
    // docs/superpowers/specs/2026-08-16-ingestion-decision-staleness-guard-design.md.
    const clusterTwoAfterConcurrentResolve: IngestionCluster = {
      members: [
        { image_id: 'pending-2', filename: 'second.jpg', status: 'active', ocr_text: null },
      ],
      edges: [],
    }
    const resolve = vi.fn()
      .mockResolvedValueOnce({ rejected: ['pending-1'], kept: [], failed: [], move_failed: [] }) // pending-1's own submit
      .mockResolvedValueOnce({ rejected: ['pending-3'], kept: [], failed: [], move_failed: [] }) // submit-all afterward
    const getIngestionClusters = vi.fn()
      // Initial load: pending-1, pending-2, pending-3 all pending, each its own cluster.
      .mockResolvedValueOnce([mockCluster, mockClusterTwo, mockClusterThree])
      // Reload after pending-1's submit: pending-2 has since gone "active" (concurrent
      // reviewer); pending-3 is still pending and still undecided in this mock.
      .mockResolvedValueOnce([clusterTwoAfterConcurrentResolve, mockClusterThree])
      // Any subsequent reload (e.g. after submit-all) -- durable fallback so the mock never
      // runs out and returns undefined, which would throw in the page's collection loop.
      .mockResolvedValue([mockClusterThree])
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue(mockStatus), // tier never changes
      getIngestionClusters,
      resolveIngestionCluster: resolve,
    })
    const user = userEvent.setup()
    render(<IngestionReviewPage memesApi={api} />)

    await waitFor(() => expect(screen.getByText('new.jpg')).toBeInTheDocument())
    // Decide all three while all are pending. Cluster/button order: pending-1 (index 0),
    // pending-2 (index 1), pending-3 (index 2).
    await user.click(screen.getAllByText('Reject')[0])
    await user.click(screen.getAllByText('Reject')[1])
    await user.click(screen.getAllByText('Reject')[2])

    // Submit only pending-1's own cluster -- triggers the reload where pending-2 goes active.
    await user.click(screen.getAllByText('Submit decisions')[0])
    await waitFor(() => expect(resolve).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(screen.getByText('second.jpg')).toBeInTheDocument())

    // pending-2's decision is still in local state (never submitted; no tier change happened,
    // so Task 2's guard doesn't apply here). Submit-all must exclude it now that its status is
    // "active", sending only pending-3.
    await user.click(screen.getByText(/Submit all decisions/))
    await user.click(screen.getByText(/Confirm\?/))

    await waitFor(() => expect(resolve).toHaveBeenCalledTimes(2))
    const [, sentDecisions] = resolve.mock.calls[1]
    expect(sentDecisions).toEqual([{ image_id: 'pending-3', decision: 'reject' }])
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
      const resolve = vi.fn().mockResolvedValue({ rejected: ['pending-1'], kept: ['pending-3'], failed: [], move_failed: [] })
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
      expect(screen.getByText('Confirm? (2 clusters, 2 images)')).toBeInTheDocument()
      await user.click(screen.getByText(/Confirm\?/))

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
      await user.click(screen.getByText(/Confirm\?/))

      await waitFor(() => expect(screen.getAllByText('Submit decisions')[0]).toBeDisabled())
    })

    it('clears submitted decisions from state so a re-appearing image is not resubmitted', async () => {
      // Regression test: `decisions` must not survive a successful submission. Otherwise a
      // stale decision for an image_id can leak into a later submission for a different
      // cluster (e.g. across a tier switch, where the same image_id can reappear in a new
      // cluster) and get submitted without the reviewer ever deciding on it there.
      const resolve = vi.fn().mockResolvedValue({ rejected: ['pending-1'], kept: [], failed: [], move_failed: [] })
      const getIngestionClusters = vi.fn()
        .mockResolvedValueOnce([mockCluster]) // initial load
        .mockResolvedValueOnce([mockCluster]) // reload after submit -- same cluster reappears
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

      await waitFor(() => expect(resolve).toHaveBeenCalledTimes(1))
      // Cluster reappeared identically after reload (as if the backend still returned it, or
      // an equivalent cluster in a new tier reused the same image_id) -- since `decisions` was
      // cleared for pending-1 on submit, this fresh render must be undecided again: the
      // per-cluster submit button is disabled and the Reject button is not shown as active.
      await waitFor(() => expect(screen.getByText('new.jpg')).toBeInTheDocument())
      expect(screen.getByText('Submit decisions')).toBeDisabled()
      expect(screen.getByText('Reject')).not.toHaveClass('bg-red-600')
    })
  })

  it('leaves a failed decision in local state for retry, and shows an error summary', async () => {
    // Regression test: once resolve() can partially succeed with a 200 response (see
    // docs/superpowers/specs/2026-08-16-ingestion-resolve-atomicity-design.md), clearing
    // decisions based on what was *sent* would silently discard ones that didn't actually
    // apply. Only ids present in the response's rejected/kept arrays should be cleared.
    const resolve = vi.fn().mockResolvedValue({
      rejected: [],
      kept: [],
      failed: [{ image_id: 'pending-1', decision: 'reject', error: 'db down' }],
      move_failed: [],
    })
    const getIngestionClusters = vi.fn()
      .mockResolvedValueOnce([mockCluster])
      .mockResolvedValueOnce([mockCluster]) // reload after submit -- cluster unchanged
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

    await waitFor(() => expect(resolve).toHaveBeenCalledTimes(1))
    // The decision failed to apply -- it must still be selected after reload, ready to retry,
    // and the page must surface that something needs attention.
    await waitFor(() => expect(screen.getByText('Reject')).toHaveClass('bg-red-600'))
    expect(screen.getByText(/1 decision\(s\) failed to apply/)).toBeInTheDocument()
  })

  it('shows a move-failed summary without treating it as a hard error', async () => {
    const resolve = vi.fn().mockResolvedValue({
      rejected: ['pending-1'],
      kept: [],
      failed: [],
      move_failed: [{ image_id: 'pending-1', error: 'file locked' }],
    })
    const getIngestionClusters = vi.fn()
      .mockResolvedValueOnce([mockCluster])
      .mockResolvedValueOnce([]) // resolved, drops out of the queue
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

    await waitFor(() => expect(resolve).toHaveBeenCalledTimes(1))
    await waitFor(() =>
      expect(screen.getByText(/were recorded as rejected even though their file move failed/)).toBeInTheDocument()
    )
    // A move failure is not a hard error -- the page still shows the (now-empty) cluster list,
    // not the full-page error screen.
    expect(screen.getByText('No Tier A clusters need review right now.')).toBeInTheDocument()
  })

  describe('error recovery', () => {
    it('shows a Retry button on load failure, and recovers when clicked', async () => {
      const getIngestionRunStatus = vi.fn()
        .mockRejectedValueOnce(new Error('network down'))
        .mockResolvedValueOnce(mockStatus)
      const api = makeMockApi({
        getIngestionRunStatus,
        getIngestionClusters: vi.fn().mockResolvedValue([mockCluster]),
      })
      const user = userEvent.setup()
      render(<IngestionReviewPage memesApi={api} />)

      await waitFor(() => expect(screen.getByText('network down')).toBeInTheDocument())
      const retryButton = screen.getByText('Retry')
      expect(retryButton).toBeInTheDocument()

      await user.click(retryButton)

      await waitFor(() => expect(screen.getByText('new.jpg')).toBeInTheDocument())
      expect(screen.queryByText('network down')).not.toBeInTheDocument()
      expect(getIngestionRunStatus).toHaveBeenCalledTimes(2)
    })
  })
})
