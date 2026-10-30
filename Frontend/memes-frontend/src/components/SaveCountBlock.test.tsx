import { render, screen, waitFor, act } from '@testing-library/react'
import { StrictMode } from 'react'
import { SaveCountBlock } from './SaveCountBlock'
import { makeMockApi } from '../test/mockApi'
import type { MemeSaves } from '../types/generated/all'

function apiReturning(result: Promise<MemeSaves>) {
  return makeMockApi({ getMemeSaves: vi.fn().mockReturnValue(result) })
}

describe('SaveCountBlock', () => {
  afterEach(() => vi.restoreAllMocks())

  it('shows the label and the count', async () => {
    const api = apiReturning(Promise.resolve({ imageId: 'a', count: 4, lastSavedAt: null }))
    render(<StrictMode><SaveCountBlock imageId="a" memesApi={api} /></StrictMode>)
    await waitFor(() => expect(screen.getByTestId('save-count-value')).toHaveTextContent('4'))
    expect(screen.getByText('Saves')).toBeInTheDocument()
  })

  it('shows a last saved line when lastSavedAt is set', async () => {
    const api = apiReturning(Promise.resolve({ imageId: 'a', count: 1, lastSavedAt: '2026-10-30T10:00:00Z' }))
    render(<SaveCountBlock imageId="a" memesApi={api} />)
    await waitFor(() => expect(screen.getByTestId('save-count-value')).toHaveTextContent('1'))
    expect(screen.getByText(/last saved/)).toBeInTheDocument()
  })

  it('shows no last saved line and still renders 0 when there are no saves', async () => {
    const api = apiReturning(Promise.resolve({ imageId: 'a', count: 0, lastSavedAt: null }))
    render(<SaveCountBlock imageId="a" memesApi={api} />)
    await waitFor(() => expect(screen.getByTestId('save-count-value')).toHaveTextContent('0'))
    expect(screen.queryByText(/last saved/)).not.toBeInTheDocument()
  })

  it('renders nothing while the read is pending', async () => {
    const api = apiReturning(new Promise(() => {}))
    const { container } = render(<SaveCountBlock imageId="a" memesApi={api} />)
    await act(async () => {})
    expect(container).toBeEmptyDOMElement()
  })

  it('renders nothing, and no alert, when the read fails', async () => {
    const api = apiReturning(Promise.reject(new Error('boom')))
    const { container } = render(<SaveCountBlock imageId="a" memesApi={api} />)
    await act(async () => {})
    expect(container).toBeEmptyDOMElement()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
