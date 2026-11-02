import { fireEvent, render, screen, within } from '@testing-library/react'
import { makeMockApi } from '../../test/mockApi'
import { makeSimilarityPair } from '../../duplicates/fixtures'
import type { Meme } from '../../types/generated/all'
import { ClusterDetailsPanel } from './ClusterDetailsPanel'

function member(id: string, text: string[] = []): Meme {
  return { id, imageUrl: `/api/images/${id}`, text, tags: [], originalFileName: `${id}.jpg`, clusterId: 1 }
}

const api = makeMockApi()

describe('ClusterDetailsPanel', () => {
  it('shows each member with its OCR text, or "no text"', () => {
    render(<ClusterDetailsPanel members={[member('a', ['hello there (0.93)']), member('b')]} entry={{ status: 'ok', pairs: [], capped: false }} memesApi={api} onRetry={() => {}} />)
    expect(screen.getByText('a.jpg')).toBeInTheDocument()
    expect(screen.getByText('hello there (0.93)')).toBeInTheDocument()
    expect(screen.getByText('no text')).toBeInTheDocument()
  })

  it('shows a loading note while the pairs load', () => {
    render(<ClusterDetailsPanel members={[member('a'), member('b')]} entry={{ status: 'loading' }} memesApi={api} onRetry={() => {}} />)
    expect(screen.getByText(/loading pair details/i)).toBeInTheDocument()
  })

  it('shows an error with a retry button of at least 44px height', () => {
    const onRetry = vi.fn()
    render(<ClusterDetailsPanel members={[member('a'), member('b')]} entry={{ status: 'error' }} memesApi={api} onRetry={onRetry} />)
    const button = screen.getByRole('button', { name: /retry/i })
    expect(button.className).toContain('min-h-11')
    fireEvent.click(button)
    expect(onRetry).toHaveBeenCalledTimes(1)
  })

  it('lists pairs closest clip distance first with chips and overlap', () => {
    const far = makeSimilarityPair({ image_id1: 'a', image_id2: 'b', overlap: 0.2, candidates: [{ distance: 0.09, source: 'clip' }] })
    const near = makeSimilarityPair({ image_id1: 'a', image_id2: 'c', overlap: 0.75, candidates: [{ distance: 0.031, source: 'clip' }, { distance: 0.082, source: 'ocr_text' }] })
    render(<ClusterDetailsPanel members={[member('a'), member('b'), member('c')]} entry={{ status: 'ok', pairs: [far, near], capped: false }} memesApi={api} onRetry={() => {}} />)
    const rows = screen.getAllByRole('listitem', { name: /pair/i })
    expect(within(rows[0]).getByText('clip 0.031')).toBeInTheDocument()
    expect(within(rows[0]).getByText('ocr_text 0.082')).toBeInTheDocument()
    expect(within(rows[0]).getByText(/75%/)).toBeInTheDocument()
    expect(within(rows[1]).getByText('clip 0.090')).toBeInTheDocument()
  })

  it('leaves out pairs that involve a member the page no longer shows', () => {
    const shown = makeSimilarityPair({ image_id1: 'a', image_id2: 'b', candidates: [{ distance: 0.05, source: 'clip' }] })
    const hidden = makeSimilarityPair({ image_id1: 'a', image_id2: 'c', candidates: [{ distance: 0.01, source: 'clip' }] })
    render(<ClusterDetailsPanel members={[member('a'), member('b')]} entry={{ status: 'ok', pairs: [hidden, shown], capped: false }} memesApi={api} onRetry={() => {}} />)
    const rows = screen.getAllByRole('listitem', { name: /pair/i })
    expect(rows).toHaveLength(1)
    expect(within(rows[0]).getByText('clip 0.050')).toBeInTheDocument()
    expect(screen.queryByText(/clip 0\.010/)).not.toBeInTheDocument()
  })

  it('says a pair is linked through other members when it has no candidates', () => {
    render(<ClusterDetailsPanel members={[member('a'), member('b')]} entry={{ status: 'ok', pairs: [makeSimilarityPair({ overlap: null })], capped: false }} memesApi={api} onRetry={() => {}} />)
    expect(screen.getByText(/linked through other members/i)).toBeInTheDocument()
    expect(screen.queryByText(/NaN/)).not.toBeInTheDocument()
    expect(screen.getByText(/no OCR overlap data/i)).toBeInTheDocument()
  })

  it('shows the over-cap notice but still lists members', () => {
    render(<ClusterDetailsPanel members={[member('a', ['kept text'])]} entry={{ status: 'ok', pairs: [], capped: true }} memesApi={api} onRetry={() => {}} />)
    expect(screen.getByText(/pair details are unavailable for clusters over 100/i)).toBeInTheDocument()
    expect(screen.getByText('kept text')).toBeInTheDocument()
  })

  it('does not show the over-cap notice for an uncapped response', () => {
    render(<ClusterDetailsPanel members={[member('a'), member('b')]} entry={{ status: 'ok', pairs: [], capped: false }} memesApi={api} onRetry={() => {}} />)
    expect(screen.queryByText(/over 100/i)).not.toBeInTheDocument()
  })

  it('renders members only when there is no entry yet', () => {
    render(<ClusterDetailsPanel members={[member('a', ['x'])]} entry={undefined} memesApi={api} onRetry={() => {}} />)
    expect(screen.getByText('x')).toBeInTheDocument()
    expect(screen.queryByText(/loading pair details/i)).not.toBeInTheDocument()
  })
})
