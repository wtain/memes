import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import IngestionReviewPage from './IngestionReviewPage'
import { makeMockApi } from '../test/mockApi'
import type { IngestionClusterPage, IngestionCluster, IngestionRunStatus } from '../types/generated/all'

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
  }
}
const page = (items: IngestionCluster[], next: string | null = null): IngestionClusterPage =>
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

  it('reloads the queue when the last visible cluster is resolved and no more pages remain', async () => {
    const getIngestionRunStatus = vi.fn()
      .mockResolvedValueOnce(runStatus)
      .mockResolvedValueOnce({ ...runStatus, stage: 'tier_b_review' })
    const getIngestionClusters = vi.fn()
      .mockResolvedValueOnce(page([cl('a')]))
      .mockResolvedValueOnce(page([]))
    const resolveIngestionCluster = vi.fn().mockResolvedValue({ rejected: ['a-1'], kept: [], failed: [], move_failed: [] })
    const api = makeMockApi({ getIngestionRunStatus, getIngestionClusters, resolveIngestionCluster })
    render(<IngestionReviewPage memesApi={api} />)
    await screen.findByText('a-1.jpg')
    await userEvent.click(screen.getByRole('button', { name: /^reject$/i }))
    await userEvent.click(screen.getByRole('button', { name: /^submit decisions$/i }))

    await waitFor(() => expect(getIngestionClusters).toHaveBeenCalledTimes(2))
    expect(getIngestionRunStatus).toHaveBeenCalledTimes(2)
    expect(await screen.findByText('Ingestion Review — Tier B')).toBeInTheDocument()
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
    const getIngestionClusters = vi.fn()
      .mockResolvedValueOnce(page([cl('a')]))
      .mockResolvedValueOnce(page([cl('a')]))
    const api = makeMockApi({ getIngestionRunStatus, getIngestionClusters })
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
