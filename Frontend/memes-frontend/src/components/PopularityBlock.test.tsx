import { render, screen, waitFor, act } from '@testing-library/react'
import { StrictMode } from 'react'
import { PopularityBlock } from './PopularityBlock'
import { makeMockApi } from '../test/mockApi'
import type { MemePopularity } from '../types/generated/all'

function apiReturning(result: Promise<MemePopularity>) {
  return makeMockApi({ getMemePopularity: vi.fn().mockReturnValue(result) })
}

describe('PopularityBlock', () => {
  afterEach(() => vi.restoreAllMocks())

  it('shows the number and one labelled line per breakdown row', async () => {
    const api = apiReturning(Promise.resolve({
      popularity: 5,
      status: 'normal',
      breakdown: [{ source: 'own', count: 1 }, { source: 'hash_in_batch', count: 3 }, { source: 'cluster_review', count: 1 }],
    }))
    render(<PopularityBlock imageId="a" memesApi={api} />)
    await waitFor(() => expect(screen.getByTestId('popularity-value')).toHaveTextContent('5'))
    expect(screen.getByText('The image itself: 1')).toBeInTheDocument()
    expect(screen.getByText('Identical files dropped together: 3')).toBeInTheDocument()
    expect(screen.getByText('Duplicates page: 1')).toBeInTheDocument()
    expect(screen.getAllByRole('listitem')).toHaveLength(3)
    expect(screen.queryByTestId('popularity-status')).not.toBeInTheDocument()
  })

  it('shows 0 and discarded with no rows for a discarded image', async () => {
    const api = apiReturning(Promise.resolve({ popularity: 0, status: 'discarded', breakdown: [] }))
    render(<PopularityBlock imageId="a" memesApi={api} />)
    await waitFor(() => expect(screen.getByTestId('popularity-value')).toHaveTextContent('0'))
    expect(screen.getByTestId('popularity-status')).toHaveTextContent('discarded')
    expect(screen.queryAllByRole('listitem')).toHaveLength(0)
  })

  it('shows the status for a flagged image', async () => {
    const api = apiReturning(Promise.resolve({ popularity: 2, status: 'flagged', breakdown: [{ source: 'own', count: 2 }] }))
    render(<PopularityBlock imageId="a" memesApi={api} />)
    await waitFor(() => expect(screen.getByTestId('popularity-status')).toHaveTextContent('flagged'))
  })

  it('renders nothing while the request is pending', async () => {
    const { container } = render(<PopularityBlock imageId="a" memesApi={apiReturning(new Promise(() => {}))} />)
    await act(async () => {})
    expect(container).toBeEmptyDOMElement()
  })

  it('renders nothing and logs nothing after a rejection', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const { container } = render(<PopularityBlock imageId="a" memesApi={apiReturning(Promise.reject(new Error('404')))} />)
    await act(async () => {})
    expect(container).toBeEmptyDOMElement()
    expect(errorSpy).not.toHaveBeenCalled()
    expect(warnSpy).not.toHaveBeenCalled()
  })

  it('shows the raw key for an unknown source', async () => {
    const api = apiReturning(Promise.resolve({ popularity: 2, status: 'normal', breakdown: [{ source: 'own', count: 1 }, { source: 'manual', count: 1 }] }))
    render(<PopularityBlock imageId="a" memesApi={api} />)
    await waitFor(() => expect(screen.getByText('manual: 1')).toBeInTheDocument())
  })

  it('fetches once in StrictMode', async () => {
    const api = makeMockApi()
    render(<StrictMode><PopularityBlock imageId="a" memesApi={api} /></StrictMode>)
    await waitFor(() => expect(screen.getByTestId('popularity-value')).toBeInTheDocument())
    expect(api.getMemePopularity).toHaveBeenCalledTimes(1)
  })
})
