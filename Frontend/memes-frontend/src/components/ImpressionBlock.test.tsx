import { render, screen, waitFor, act } from '@testing-library/react'
import { StrictMode } from 'react'
import { ImpressionBlock } from './ImpressionBlock'
import { makeMockApi } from '../test/mockApi'
import type { MemeImpressions } from '../types/generated/all'

function apiReturning(result: Promise<MemeImpressions>) {
  return makeMockApi({ getMemeImpressions: vi.fn().mockReturnValue(result) })
}

const base = { imageId: 'a', count: 12, countWindow: 5, windowDays: 30, lastShownAt: null }

describe('ImpressionBlock', () => {
  afterEach(() => vi.restoreAllMocks())

  it('shows the label, the count and the window count', async () => {
    const api = apiReturning(Promise.resolve(base))
    render(<StrictMode><ImpressionBlock imageId="a" memesApi={api} /></StrictMode>)
    await waitFor(() => expect(screen.getByTestId('impression-count')).toHaveTextContent('12'))
    expect(screen.getByText('Shown')).toBeInTheDocument()
    expect(screen.getByTestId('impression-window')).toHaveTextContent('5 in the last 30 days')
  })

  it('shows a last shown line when lastShownAt is set', async () => {
    const api = apiReturning(Promise.resolve({ ...base, lastShownAt: '2026-11-01T10:00:00Z' }))
    render(<ImpressionBlock imageId="a" memesApi={api} />)
    await waitFor(() => expect(screen.getByTestId('impression-count')).toHaveTextContent('12'))
    expect(screen.getByTestId('impression-last-shown')).toHaveTextContent(/last shown/)
  })

  it('still renders 0 and no last shown line when it was never shown', async () => {
    const api = apiReturning(Promise.resolve({ ...base, count: 0, countWindow: 0 }))
    render(<ImpressionBlock imageId="a" memesApi={api} />)
    await waitFor(() => expect(screen.getByTestId('impression-count')).toHaveTextContent('0'))
    expect(screen.queryByTestId('impression-last-shown')).not.toBeInTheDocument()
  })

  it('renders nothing while the read is pending', async () => {
    const api = apiReturning(new Promise(() => {}))
    const { container } = render(<ImpressionBlock imageId="a" memesApi={api} />)
    await act(async () => {})
    expect(container).toBeEmptyDOMElement()
  })

  it('renders nothing, and no alert, when the read fails', async () => {
    const api = apiReturning(Promise.reject(new Error('boom')))
    const { container } = render(<ImpressionBlock imageId="a" memesApi={api} />)
    await act(async () => {})
    expect(container).toBeEmptyDOMElement()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
