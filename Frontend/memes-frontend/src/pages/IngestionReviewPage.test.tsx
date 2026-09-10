import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import IngestionReviewPage from './IngestionReviewPage'
import { makeMockApi } from '../test/mockApi'
import type {
  IngestionClusterPage, IngestionCluster, IngestionRunStatus, IngestionTierBReviewItem,
} from '../types/generated/all'

// Virtuoso mock: mirrors src/components/MemesDuplicatesList.test.tsx. The mock never calls the
// callback props itself -- it captures `endReached` so the pagination tests can drive it, and
// renders every item so assertions can find them (jsdom has no real scrolling).
const capturedProps: { endReached?: () => void } = {}
vi.mock('react-virtuoso', () => ({
  Virtuoso: (props: {
    data: unknown[]
    itemContent: (index: number, item: unknown) => React.ReactNode
    endReached?: () => void
  }) => {
    capturedProps.endReached = props.endReached
    return <div>{props.data.map((item, i) => <div key={i}>{props.itemContent(i, item)}</div>)}</div>
  },
}))

const runStatus: IngestionRunStatus = {
  run_id: 'r1', status: 'started', stage: 'tier_a_review', stats: {}, created_at: '', completed_at: null,
}

function cl(id: string, dist = 0.05): IngestionCluster {
  return {
    members: [
      { image_id: `${id}-1`, filename: `${id}-1.jpg`, status: 'pending', ocr_text: 'текст' },
      { image_id: `${id}-2`, filename: `${id}-2.jpg`, status: 'active', ocr_text: 'текст' },
    ],
    edges: [{ image_id1: `${id}-1`, image_id2: `${id}-2`, distance: dist, match_source: 'clip' }],
    total_members: 2,
  }
}
const page = (items: IngestionCluster[], next: string | null = null): IngestionClusterPage =>
  ({ items, next_cursor: next, has_next: next !== null })

const tierBStatus = { ...runStatus, stage: 'tier_b_review' }

function tbItem(sid: string, candIds: [string, 'pending' | 'active'][], dist = 0.08): IngestionTierBReviewItem {
  return {
    image: { image_id: sid, filename: `${sid}.jpg`, status: 'pending', ocr_text: null },
    candidates: candIds.map(([cid, st], i) => ({
      member: { image_id: cid, filename: `${cid}.jpg`, status: st, ocr_text: null },
      distance: dist + i * 0.01, match_source: 'in_batch',
    })),
    total_candidates: candIds.length,
  }
}
const tbPage = (items: IngestionTierBReviewItem[], next: string | null = null) =>
  ({ items, next_cursor: next, has_next: next !== null })

beforeEach(() => {
  capturedProps.endReached = undefined
  vi.clearAllMocks()
})

describe('IngestionReviewPage', () => {
  it('loads the first page and renders clusters', async () => {
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue(runStatus),
      getIngestionClusters: vi.fn().mockResolvedValue(page([cl('a'), cl('b')])),
    })
    render(<IngestionReviewPage memesApi={api} />)
    expect(await screen.findByText('a-1.jpg')).toBeInTheDocument()
    expect(screen.getByText('b-1.jpg')).toBeInTheDocument()
    expect(api.getIngestionClusters).toHaveBeenCalledWith('tier_a', undefined)
  })

  it('shows the full OCR text for a member, not a clamped preview', async () => {
    const longOcr = 'Очень длинный текст который никогда не должен обрезаться в этом интерфейсе полностью'
    const clWithOcr: IngestionCluster = {
      members: [{ image_id: 'x-1', filename: 'x-1.jpg', status: 'pending', ocr_text: longOcr }],
      edges: [],
      total_members: 1,
    }
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue(runStatus),
      getIngestionClusters: vi.fn().mockResolvedValue(page([clWithOcr])),
    })
    render(<IngestionReviewPage memesApi={api} />)
    expect(await screen.findByText(longOcr)).toBeInTheDocument()
    expect(document.querySelector('.line-clamp-3')).toBeNull()
  })

  it('fetches the next page when the list end is reached', async () => {
    const getIngestionClusters = vi.fn()
      .mockResolvedValueOnce(page([cl('a')], 'CURSOR1'))
      .mockResolvedValueOnce(page([cl('b')]))
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue(runStatus),
      getIngestionClusters,
    })
    render(<IngestionReviewPage memesApi={api} />)
    await screen.findByText('a-1.jpg')

    await act(async () => { capturedProps.endReached?.() })

    await waitFor(() => expect(getIngestionClusters).toHaveBeenCalledWith('tier_a', 'CURSOR1'))
    expect(await screen.findByText('b-1.jpg')).toBeInTheDocument()
  })

  it('exposes a "Load more" button while more pages remain and fetches on click', async () => {
    const getIngestionClusters = vi.fn()
      .mockResolvedValueOnce(page([cl('a')], 'CURSOR1'))
      .mockResolvedValueOnce(page([cl('b')]))
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue(runStatus),
      getIngestionClusters,
    })
    render(<IngestionReviewPage memesApi={api} />)
    await screen.findByText('a-1.jpg')

    await userEvent.click(screen.getByRole('button', { name: /load more/i }))

    await waitFor(() => expect(getIngestionClusters).toHaveBeenCalledWith('tier_a', 'CURSOR1'))
    expect(await screen.findByText('b-1.jpg')).toBeInTheDocument()
    // last page reached -- the button is gone
    expect(screen.queryByRole('button', { name: /load more/i })).toBeNull()
  })

  it('optimistically removes a fully-resolved cluster on submit and does not refetch page 1', async () => {
    const getIngestionClusters = vi.fn().mockResolvedValue(page([cl('a'), cl('b')]))
    const resolveIngestionCluster = vi.fn().mockResolvedValue({ rejected: ['a-1'], kept: [], failed: [], move_failed: [] })
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue(runStatus),
      getIngestionClusters, resolveIngestionCluster,
    })
    render(<IngestionReviewPage memesApi={api} />)
    await screen.findByText('a-1.jpg')
    await userEvent.click(screen.getAllByRole('button', { name: /^reject$/i })[0])
    await userEvent.click(screen.getAllByRole('button', { name: /^submit decisions$/i })[0])
    await waitFor(() => expect(screen.queryByText('a-1.jpg')).toBeNull())
    expect(screen.getByText('b-1.jpg')).toBeInTheDocument()
    expect(getIngestionClusters).toHaveBeenCalledTimes(1) // no reload on the happy path
  })

  it('keeps "Submitting…" on the submitted cluster, not whatever slides into its list slot', async () => {
    // Regression: `submitting` must be keyed by the cluster object. With an index key, removing
    // cluster a mid-request shifts cluster b into index 0 and b's button wrongly shows "Submitting…".
    const getIngestionClusters = vi.fn().mockResolvedValue(page([cl('a'), cl('b')]))
    const resolveIngestionCluster = vi.fn().mockImplementation(() => new Promise(() => {})) // never resolves
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue(runStatus),
      getIngestionClusters, resolveIngestionCluster,
    })
    render(<IngestionReviewPage memesApi={api} />)
    await screen.findByText('a-1.jpg')
    await userEvent.click(screen.getAllByRole('button', { name: /^reject$/i })[0]) // a-1
    await userEvent.click(screen.getAllByRole('button', { name: /^reject$/i })[1]) // b-1
    await userEvent.click(screen.getAllByRole('button', { name: /^submit decisions$/i })[0]) // submit cluster a

    await waitFor(() => expect(screen.queryByText('a-1.jpg')).toBeNull()) // a optimistically removed
    const bSubmit = screen.getByRole('button', { name: /^submit decisions$/i }) // only cluster b's remains
    expect(bSubmit).toBeEnabled()
    expect(bSubmit).toHaveTextContent(/^submit decisions$/i) // not "Submitting…"
  })

  it('prunes a decision the server silently skipped so it cannot be resubmitted', async () => {
    // The backend can resolve a submitted decision to a no-op (target no longer pending -- e.g.
    // reject_image's own guard returns None): the id comes back in none of
    // rejected/kept/failed/move_failed. The payload-scoped prune must still clear it.
    const clMixed: IngestionCluster = {
      members: [
        { image_id: 'm-1', filename: 'm-1.jpg', status: 'pending', ocr_text: null },
        { image_id: 'm-2', filename: 'm-2.jpg', status: 'pending', ocr_text: null },
        { image_id: 'm-3', filename: 'm-3.jpg', status: 'active', ocr_text: null },
      ],
      edges: [],
      total_members: 3,
    }
    const getIngestionClusters = vi.fn().mockResolvedValue(page([clMixed]))
    const resolveIngestionCluster = vi.fn().mockResolvedValue({ rejected: [], kept: [], failed: [], move_failed: [] })
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue(runStatus),
      getIngestionClusters, resolveIngestionCluster,
    })
    render(<IngestionReviewPage memesApi={api} />)
    await screen.findByText('m-1.jpg')

    // decide only m-1 -> cluster is not fully resolved -> not optimistically removed -> stays visible
    await userEvent.click(screen.getAllByRole('button', { name: /^reject$/i })[0])
    expect(screen.getAllByRole('button', { name: /^reject$/i })[0].className).toContain('bg-red-600')
    await userEvent.click(screen.getByRole('button', { name: /^submit decisions$/i }))

    await waitFor(() => expect(resolveIngestionCluster).toHaveBeenCalledTimes(1))
    await waitFor(() =>
      expect(screen.getAllByRole('button', { name: /^reject$/i })[0].className).not.toContain('bg-red-600')
    )
    expect(getIngestionClusters).toHaveBeenCalledTimes(1) // no reload
    expect(screen.queryByText(/Submit all decisions/i)).toBeNull() // no decision left anywhere
  })

  it('does not offer decisions for a member a reload shows is already resolved', async () => {
    // Regression (decision-staleness guard): after a member is resolved, a reload that returns it
    // as read-only `active` context must not carry a prior decision forward or show Keep/Reject.
    const clA: IngestionCluster = {
      members: [{ image_id: 'p-1', filename: 'p-1.jpg', status: 'pending', ocr_text: null }], edges: [], total_members: 1,
    }
    const clB: IngestionCluster = {
      members: [{ image_id: 'p-2', filename: 'p-2.jpg', status: 'pending', ocr_text: null }], edges: [], total_members: 1,
    }
    const clAfter: IngestionCluster = {
      members: [
        { image_id: 'p-2', filename: 'p-2.jpg', status: 'active', ocr_text: null },
        { image_id: 'p-3', filename: 'p-3.jpg', status: 'pending', ocr_text: null },
      ],
      edges: [],
      total_members: 2,
    }
    const getIngestionClusters = vi.fn()
      .mockResolvedValueOnce(page([clA, clB]))
      .mockResolvedValueOnce(page([clAfter]))
    const resolveIngestionCluster = vi.fn()
      .mockResolvedValueOnce({ rejected: ['p-1'], kept: [], failed: [], move_failed: [] })
      .mockResolvedValueOnce({ rejected: ['p-2'], kept: [], failed: [], move_failed: [] })
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue(runStatus),
      getIngestionClusters, resolveIngestionCluster,
    })
    render(<IngestionReviewPage memesApi={api} />)
    await screen.findByText('p-1.jpg')

    await userEvent.click(screen.getAllByRole('button', { name: /^reject$/i })[0]) // p-1
    await userEvent.click(screen.getAllByRole('button', { name: /^reject$/i })[1]) // p-2 (left un-submitted for now)
    await userEvent.click(screen.getAllByRole('button', { name: /^submit decisions$/i })[0]) // submit clA
    await waitFor(() => expect(screen.queryByText('p-1.jpg')).toBeNull())

    await userEvent.click(screen.getByRole('button', { name: /^submit decisions$/i })) // submit clB -> empties queue -> reload
    await waitFor(() => expect(getIngestionClusters).toHaveBeenCalledTimes(2))
    await screen.findByText('p-3.jpg')

    expect(screen.getAllByRole('button', { name: /^reject$/i })).toHaveLength(1) // only p-3 is actionable
    expect(screen.queryByText(/Submit all decisions/i)).toBeNull()
  })

  it('reloads the queue when the last visible cluster is resolved and no more pages remain', async () => {
    const getIngestionRunStatus = vi.fn()
      .mockResolvedValueOnce(runStatus)
      .mockResolvedValueOnce({ ...runStatus, stage: 'tier_b_review' })
    const getIngestionClusters = vi.fn().mockResolvedValueOnce(page([cl('a')]))
    const getIngestionTierBReview = vi.fn().mockResolvedValue(tbPage([]))
    const resolveIngestionCluster = vi.fn().mockResolvedValue({ rejected: ['a-1'], kept: [], failed: [], move_failed: [] })
    const api = makeMockApi({ getIngestionRunStatus, getIngestionClusters, getIngestionTierBReview, resolveIngestionCluster })
    render(<IngestionReviewPage memesApi={api} />)
    await screen.findByText('a-1.jpg')
    await userEvent.click(screen.getByRole('button', { name: /^reject$/i }))
    await userEvent.click(screen.getByRole('button', { name: /^submit decisions$/i }))

    // last visible cluster resolved + no more pages -> the page reloads and, because run status
    // has advanced, the reload pulls the Tier B queue rather than a second cluster fetch.
    await waitFor(() => expect(getIngestionRunStatus).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(getIngestionTierBReview).toHaveBeenCalled())
    expect(getIngestionClusters).toHaveBeenCalledTimes(1)
    expect(await screen.findByText('Ingestion Review — Tier B')).toBeInTheDocument()
  })

  it('reloads (page 1) when "Submit all" clears the loaded page but more pages remain', async () => {
    // Regression: without the reload, clusters.length === 0 unmounts <Virtuoso> (endReached dead),
    // the "no clusters" message needs !hasNext, and the old reload gate needed !hasNext too --
    // leaving just a bare "Load more" button. The reload pulls the next page-1 work instead.
    const getIngestionClusters = vi.fn()
      .mockResolvedValueOnce(page([cl('a'), cl('b')], 'CURSOR1')) // has_next: true
      .mockResolvedValueOnce(page([cl('c')]))
    const resolveIngestionCluster = vi.fn().mockResolvedValue({ rejected: ['a-1', 'b-1'], kept: [], failed: [], move_failed: [] })
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue(runStatus),
      getIngestionClusters, resolveIngestionCluster,
    })
    render(<IngestionReviewPage memesApi={api} />)
    await screen.findByText('a-1.jpg')
    await userEvent.click(screen.getAllByRole('button', { name: /^reject$/i })[0])
    await userEvent.click(screen.getAllByRole('button', { name: /^reject$/i })[1])
    await userEvent.click(screen.getByRole('button', { name: /submit all decisions/i }))
    await userEvent.click(screen.getByRole('button', { name: /confirm/i }))

    await waitFor(() => expect(getIngestionClusters).toHaveBeenCalledTimes(2))
    expect(await screen.findByText('c-1.jpg')).toBeInTheDocument()
    expect(screen.queryByText('a-1.jpg')).toBeNull()
  })

  it('renders and submits across several small clusters split from one blob', async () => {
    // three subgroups the server split out of one union-find component
    const subgroups: IngestionCluster[] = [1, 2, 3].map((n) => ({
      members: [{ image_id: `s${n}-1`, filename: `s${n}-1.jpg`, status: 'pending', ocr_text: null }],
      edges: [],
      total_members: 1,
    }))
    const getIngestionClusters = vi.fn().mockResolvedValue(page(subgroups))
    const resolveIngestionCluster = vi.fn().mockResolvedValue({
      rejected: ['s1-1', 's2-1', 's3-1'], kept: [], failed: [], move_failed: [],
    })
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue(runStatus),
      getIngestionClusters, resolveIngestionCluster,
    })
    render(<IngestionReviewPage memesApi={api} />)
    await screen.findByText('s1-1.jpg')
    expect(screen.getByText('s2-1.jpg')).toBeInTheDocument()
    expect(screen.getByText('s3-1.jpg')).toBeInTheDocument()

    for (const btn of screen.getAllByRole('button', { name: /^reject$/i })) {
      await userEvent.click(btn)
    }
    await userEvent.click(screen.getByRole('button', { name: /submit all decisions/i }))
    await userEvent.click(screen.getByRole('button', { name: /confirm/i }))

    await waitFor(() => {
      const [, payload] = resolveIngestionCluster.mock.calls[0]
      expect(payload).toHaveLength(3)
    })
  })

  it('submits only the decided members of a cluster, not the ones left unchanged', async () => {
    const cl3: IngestionCluster = {
      members: [
        { image_id: 'q-1', filename: 'q-1.jpg', status: 'pending', ocr_text: null },
        { image_id: 'q-2', filename: 'q-2.jpg', status: 'pending', ocr_text: null },
        { image_id: 'q-3', filename: 'q-3.jpg', status: 'pending', ocr_text: null },
      ],
      edges: [],
      total_members: 3,
    }
    const getIngestionClusters = vi.fn().mockResolvedValue(page([cl3]))
    const resolveIngestionCluster = vi.fn().mockResolvedValue({ rejected: ['q-1'], kept: [], failed: [], move_failed: [] })
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue(runStatus),
      getIngestionClusters, resolveIngestionCluster,
    })
    render(<IngestionReviewPage memesApi={api} />)
    await screen.findByText('q-1.jpg')

    await userEvent.click(screen.getAllByRole('button', { name: /^reject$/i })[0]) // q-1 only
    await userEvent.click(screen.getByRole('button', { name: /^submit decisions$/i }))

    await waitFor(() => expect(resolveIngestionCluster).toHaveBeenCalledTimes(1))
    // exactly the one decided member -- q-2 and q-3 (left unchanged) are not in the payload
    expect(resolveIngestionCluster).toHaveBeenCalledWith('tier_a', [{ image_id: 'q-1', decision: 'reject' }])
  })

  it('"Submit all" resolves a partial cluster and a full one together, keeping the partial visible', async () => {
    const partial: IngestionCluster = {
      members: [
        { image_id: 'p-1', filename: 'p-1.jpg', status: 'pending', ocr_text: null },
        { image_id: 'p-2', filename: 'p-2.jpg', status: 'pending', ocr_text: null },
      ],
      edges: [],
      total_members: 2,
    }
    const getIngestionClusters = vi.fn().mockResolvedValue(page([partial, cl('f')]))
    const resolveIngestionCluster = vi.fn().mockResolvedValue({ rejected: ['p-1', 'f-1'], kept: [], failed: [], move_failed: [] })
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue(runStatus),
      getIngestionClusters, resolveIngestionCluster,
    })
    render(<IngestionReviewPage memesApi={api} />)
    await screen.findByText('p-1.jpg')

    await userEvent.click(screen.getAllByRole('button', { name: /^reject$/i })[0]) // p-1 (partial: p-2 left)
    await userEvent.click(screen.getAllByRole('button', { name: /^reject$/i })[2]) // f-1 (fully resolves cl('f'))
    await userEvent.click(screen.getByRole('button', { name: /submit all decisions/i })) // arm confirm
    await userEvent.click(screen.getByRole('button', { name: /confirm/i }))

    await waitFor(() => expect(resolveIngestionCluster).toHaveBeenCalledTimes(1))
    const [, payload] = resolveIngestionCluster.mock.calls[0]
    expect(payload).toEqual(expect.arrayContaining([
      { image_id: 'p-1', decision: 'reject' }, { image_id: 'f-1', decision: 'reject' },
    ]))
    expect(payload).toHaveLength(2) // p-2, left unchanged, is not submitted

    // the fully-resolved cluster is gone; the partial one stays with its undecided member actionable
    await waitFor(() => expect(screen.queryByText('f-1.jpg')).toBeNull())
    expect(screen.getByText('p-1.jpg')).toBeInTheDocument()
    expect(screen.getByText('p-1.jpg').parentElement).toHaveTextContent('rejected') // decided -> read-only
    expect(screen.getByRole('button', { name: /^reject$/i })).toBeInTheDocument() // p-2 still decidable
    expect(getIngestionClusters).toHaveBeenCalledTimes(1) // partial cluster still visible -> no reload
  })

  it('flips just-resolved members of a surviving cluster to read-only context', async () => {
    // A partially-resolved cluster stays in the list; its resolved members must stop offering
    // Keep/Reject (else, with the decision highlight pruned, the tile reads as "submit failed").
    const cl3: IngestionCluster = {
      members: [
        { image_id: 'q-1', filename: 'q-1.jpg', status: 'pending', ocr_text: null },
        { image_id: 'q-2', filename: 'q-2.jpg', status: 'pending', ocr_text: null },
        { image_id: 'q-3', filename: 'q-3.jpg', status: 'pending', ocr_text: null },
      ],
      edges: [],
      total_members: 3,
    }
    const getIngestionClusters = vi.fn().mockResolvedValue(page([cl3]))
    const resolveIngestionCluster = vi.fn().mockResolvedValue({ rejected: ['q-1'], kept: ['q-2'], failed: [], move_failed: [] })
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue(runStatus),
      getIngestionClusters, resolveIngestionCluster,
    })
    render(<IngestionReviewPage memesApi={api} />)
    await screen.findByText('q-1.jpg')

    expect(screen.getAllByRole('button', { name: /^reject$/i })).toHaveLength(3)
    await userEvent.click(screen.getAllByRole('button', { name: /^reject$/i })[0]) // q-1
    await userEvent.click(screen.getAllByRole('button', { name: /^keep$/i })[1]) // q-2
    await userEvent.click(screen.getByRole('button', { name: /^submit decisions$/i }))

    await waitFor(() => expect(resolveIngestionCluster).toHaveBeenCalledTimes(1))
    // q-1 and q-2 lose their controls; q-3 keeps its; the cluster stays in the list.
    await waitFor(() => expect(screen.getAllByRole('button', { name: /^reject$/i })).toHaveLength(1))
    expect(screen.getByText('q-1.jpg')).toBeInTheDocument()
    // status label stays truthful per the server outcome, not a blanket "active"
    expect(screen.getByText('q-1.jpg').parentElement).toHaveTextContent('rejected')
    expect(screen.getByText('q-2.jpg').parentElement).toHaveTextContent('active')
    expect(getIngestionClusters).toHaveBeenCalledTimes(1) // no reload -- cluster still visible
  })

  it('re-inserts the cluster and shows a message when the server reports a failed decision', async () => {
    const getIngestionClusters = vi.fn().mockResolvedValue(page([cl('a')]))
    const resolveIngestionCluster = vi.fn().mockResolvedValue({
      rejected: [], kept: [], failed: [{ image_id: 'a-1', decision: 'reject', error: 'boom' }], move_failed: [],
    })
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue(runStatus),
      getIngestionClusters, resolveIngestionCluster,
    })
    render(<IngestionReviewPage memesApi={api} />)
    await screen.findByText('a-1.jpg')
    await userEvent.click(screen.getByRole('button', { name: /^reject$/i }))
    await userEvent.click(screen.getByRole('button', { name: /^submit decisions$/i }))
    expect(await screen.findByText(/failed to apply/i)).toBeInTheDocument()
    expect(screen.getByText('a-1.jpg')).toBeInTheDocument() // back in the list
    // the failed decision stays selected, ready to retry
    expect(screen.getByRole('button', { name: /^reject$/i }).className).toContain('bg-red-600')
    expect(getIngestionClusters).toHaveBeenCalledTimes(1) // still visible clusters -> no reload
  })

  it('rolls the cluster back and shows the error inline when the submit request throws', async () => {
    const getIngestionClusters = vi.fn().mockResolvedValue(page([cl('a')]))
    const resolveIngestionCluster = vi.fn().mockRejectedValue(new Error('network down'))
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue(runStatus),
      getIngestionClusters, resolveIngestionCluster,
    })
    render(<IngestionReviewPage memesApi={api} />)
    await screen.findByText('a-1.jpg')
    await userEvent.click(screen.getByRole('button', { name: /^reject$/i }))
    await userEvent.click(screen.getByRole('button', { name: /^submit decisions$/i }))
    expect(await screen.findByText('network down')).toBeInTheDocument()
    expect(screen.getByText('a-1.jpg')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^reject$/i }).className).toContain('bg-red-600')
  })

  it('keeps the "Submit all" bar and submits every decision after confirming', async () => {
    const getIngestionClusters = vi.fn()
      .mockResolvedValueOnce(page([cl('a'), cl('b')]))
      .mockResolvedValue(page([]))
    const resolveIngestionCluster = vi.fn().mockResolvedValue({ rejected: ['a-1', 'b-1'], kept: [], failed: [], move_failed: [] })
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue(runStatus),
      getIngestionClusters, resolveIngestionCluster,
    })
    render(<IngestionReviewPage memesApi={api} />)
    await screen.findByText('a-1.jpg')
    await userEvent.click(screen.getAllByRole('button', { name: /^reject$/i })[0])
    await userEvent.click(screen.getAllByRole('button', { name: /^reject$/i })[1])
    await userEvent.click(screen.getByRole('button', { name: /submit all decisions/i })) // arm confirm
    await userEvent.click(screen.getByRole('button', { name: /confirm/i }))
    await waitFor(() => expect(resolveIngestionCluster).toHaveBeenCalledWith('tier_a',
      expect.arrayContaining([
        { image_id: 'a-1', decision: 'reject' }, { image_id: 'b-1', decision: 'reject' },
      ])))
    await waitFor(() => expect(screen.queryByText('a-1.jpg')).toBeNull())
  })

  it('shows a move-failed summary without treating it as a hard error', async () => {
    const getIngestionClusters = vi.fn()
      .mockResolvedValueOnce(page([cl('a')]))
      .mockResolvedValue(page([]))
    const resolveIngestionCluster = vi.fn().mockResolvedValue({
      rejected: ['a-1'], kept: [], failed: [],
      move_failed: [{ image_id: 'a-1', error: 'file locked' }],
    })
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue(runStatus),
      getIngestionClusters, resolveIngestionCluster,
    })
    render(<IngestionReviewPage memesApi={api} />)
    await screen.findByText('a-1.jpg')
    await userEvent.click(screen.getByRole('button', { name: /^reject$/i }))
    await userEvent.click(screen.getByRole('button', { name: /^submit decisions$/i }))
    expect(await screen.findByText(/file move failed/i)).toBeInTheDocument()
    expect(screen.getByText(/No Tier A clusters need review right now\./i)).toBeInTheDocument()
  })

  it('shows the no-run message when there is no active ingestion run', async () => {
    const api = makeMockApi({ getIngestionRunStatus: vi.fn().mockResolvedValue(null) })
    render(<IngestionReviewPage memesApi={api} />)
    expect(await screen.findByText(/no ingestion run/i)).toBeInTheDocument()
  })

  it('shows the OCR pre-pass waiting message without fetching clusters', async () => {
    const getIngestionClusters = vi.fn()
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue({ ...runStatus, stage: 'ocr_prepass' }),
      getIngestionClusters,
    })
    render(<IngestionReviewPage memesApi={api} />)
    expect(await screen.findByText(/OCR is running/i)).toBeInTheDocument()
    expect(getIngestionClusters).not.toHaveBeenCalled()
  })

  it('clears local decisions when the review tier changes', async () => {
    const getIngestionRunStatus = vi.fn()
      .mockResolvedValueOnce({ ...runStatus, stage: 'tier_a_review' })
      .mockResolvedValueOnce({ ...runStatus, stage: 'tier_b_review' })
    const getIngestionClusters = vi.fn().mockResolvedValueOnce(page([cl('a')]))
    // After the tier flips, the Tier B queue is what renders -- give it a subject tile so there
    // is still a Keep button to assert the (now-cleared) decision highlight against.
    const getIngestionTierBReview = vi.fn().mockResolvedValue(tbPage([tbItem('a', [['a-2', 'active']])]))
    const api = makeMockApi({ getIngestionRunStatus, getIngestionClusters, getIngestionTierBReview })
    const { rerender } = render(<IngestionReviewPage memesApi={api} />)
    await screen.findByText('a-1.jpg')
    await userEvent.click(screen.getByRole('button', { name: /^keep$/i }))
    expect(screen.getByRole('button', { name: /^keep$/i }).className).toContain('bg-green-600')

    rerender(<IngestionReviewPage memesApi={api} key="2" />)
    await screen.findByText('Ingestion Review — Tier B')
    await waitFor(() => {
      const keep = screen.getAllByRole('button', { name: /^keep$/i })[0]
      expect(keep.className).not.toContain('bg-green-600')
    })
  })

  it('opens a lightbox with the full image when a thumbnail is clicked, and closes it', async () => {
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue(runStatus),
      getIngestionClusters: vi.fn().mockResolvedValue(page([cl('a')])),
    })
    render(<IngestionReviewPage memesApi={api} />)
    await screen.findByText('a-1.jpg')

    await userEvent.click(screen.getByRole('button', { name: /open a-1\.jpg full size/i }))

    // The peek modal renders the filename as a heading and an enlarged image alongside a ✕ close.
    const modal = (await screen.findByRole('heading', { name: 'a-1.jpg' })).closest('div.fixed') as HTMLElement
    expect(within(modal).getByRole('img')).toHaveAttribute('src', api.getImageUrlById('a-1'))

    await userEvent.click(within(modal).getByText('✕'))
    await waitFor(() => expect(screen.queryByRole('heading', { name: 'a-1.jpg' })).toBeNull())
  })

  it('disables the per-cluster submit until a decision is made', async () => {
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue(runStatus),
      getIngestionClusters: vi.fn().mockResolvedValue(page([cl('a')])),
    })
    render(<IngestionReviewPage memesApi={api} />)
    await screen.findByText('a-1.jpg')
    expect(screen.getByRole('button', { name: /^submit decisions$/i })).toBeDisabled()
  })

  it('shows a Retry button on load failure and recovers when clicked', async () => {
    const getIngestionRunStatus = vi.fn()
      .mockRejectedValueOnce(new Error('boom'))
      .mockResolvedValueOnce(runStatus)
    const api = makeMockApi({
      getIngestionRunStatus,
      getIngestionClusters: vi.fn().mockResolvedValue(page([cl('a')])),
    })
    render(<IngestionReviewPage memesApi={api} />)
    await screen.findByText('boom')
    await userEvent.click(screen.getByRole('button', { name: /retry/i }))
    expect(await screen.findByText('a-1.jpg')).toBeInTheDocument()
    expect(screen.queryByText('boom')).toBeNull()
  })
})

describe('IngestionReviewPage — tier B', () => {
  it('renders per-image cards from the tier-b review endpoint, not clusters', async () => {
    const getIngestionTierBReview = vi.fn().mockResolvedValue(tbPage([tbItem('s1', [['c1', 'active']])]))
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue(tierBStatus),
      getIngestionTierBReview,
    })
    render(<IngestionReviewPage memesApi={api} />)
    expect(await screen.findByText('s1.jpg')).toBeInTheDocument()
    expect(screen.getByText('c1.jpg')).toBeInTheDocument()
    expect(getIngestionTierBReview).toHaveBeenCalledWith(undefined)
  })

  it('removes a card once its subject is decided and submitted', async () => {
    const getIngestionTierBReview = vi.fn().mockResolvedValue(tbPage([tbItem('s1', [['c1', 'active']]), tbItem('s2', [['c3', 'active']])]))
    const resolveIngestionCluster = vi.fn().mockResolvedValue({ rejected: ['s1'], kept: [], failed: [], move_failed: [] })
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue(tierBStatus),
      getIngestionTierBReview, resolveIngestionCluster,
    })
    render(<IngestionReviewPage memesApi={api} />)
    await screen.findByText('s1.jpg')
    await userEvent.click(screen.getAllByRole('button', { name: /^reject$/i })[0])  // s1 subject
    await userEvent.click(screen.getAllByRole('button', { name: /^submit decisions$/i })[0])
    await waitFor(() => expect(resolveIngestionCluster).toHaveBeenCalledWith('tier_b', [{ image_id: 's1', decision: 'reject' }]))
    await waitFor(() => expect(screen.queryByText('s1.jpg')).toBeNull())
    expect(screen.getByText('s2.jpg')).toBeInTheDocument()
  })

  it('a decision on an in-batch candidate shown on two cards reflects on both', async () => {
    // s1's card lists c1 (pending); c1 also has its own card
    const getIngestionTierBReview = vi.fn().mockResolvedValue(
      tbPage([tbItem('s1', [['c1', 'pending']]), tbItem('c1', [['x9', 'active']])]))
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue(tierBStatus),
      getIngestionTierBReview,
    })
    render(<IngestionReviewPage memesApi={api} />)
    await screen.findByText('s1.jpg')
    // reject c1 from s1's card (the 2nd reject button: [0]=s1 subject, [1]=c1 candidate, [2]=c1 subject-card)
    const rejects = screen.getAllByRole('button', { name: /^reject$/i })
    await userEvent.click(rejects[1])
    // both c1 tiles now show the reject highlight
    await waitFor(() => {
      const highlighted = screen.getAllByRole('button', { name: /^reject$/i }).filter(b => b.className.includes('bg-red-600'))
      expect(highlighted.length).toBe(2)
    })
  })
})
