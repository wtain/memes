import { render, screen, waitFor, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
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

  describe('set-popularity control', () => {
    const base: MemePopularity = { popularity: 2, status: 'normal', breakdown: [{ source: 'own', count: 2 }] }
    const saved: MemePopularity = {
      popularity: 30,
      status: 'normal',
      breakdown: [{ source: 'own', count: 2 }, { source: 'adjustment', count: 28 }],
    }

    async function renderBlock(initial: MemePopularity, setImpl = vi.fn().mockResolvedValue(saved)) {
      const api = makeMockApi({ getMemePopularity: vi.fn().mockResolvedValue(initial), setMemePopularity: setImpl })
      render(<PopularityBlock imageId="a" memesApi={api} />)
      await waitFor(() => expect(screen.getByTestId('popularity-value')).toBeInTheDocument())
      return api
    }

    async function typeValue(user: ReturnType<typeof userEvent.setup>, value: string) {
      const input = screen.getByRole('spinbutton')
      await user.clear(input)
      if (value) await user.type(input, value)
    }

    it('shows an input with the current value and a Save button, disabled while unchanged', async () => {
      await renderBlock(base)
      expect(screen.getByRole('spinbutton')).toHaveValue(2)
      expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
    })

    it('shows the control for a flagged image', async () => {
      await renderBlock({ ...base, status: 'flagged' })
      expect(screen.getByRole('spinbutton')).toBeInTheDocument()
    })

    it('has no control for a discarded image', async () => {
      await renderBlock({ popularity: 0, status: 'discarded', breakdown: [] })
      expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'Save' })).not.toBeInTheDocument()
    })

    it.each(['0', '1000001', ''])('disables Save for the out-of-range value "%s"', async value => {
      const user = userEvent.setup()
      await renderBlock(base)
      await typeValue(user, value)
      expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
    })

    it('saves once and shows the response with a signed adjustment line', async () => {
      const user = userEvent.setup()
      const api = await renderBlock(base)
      await typeValue(user, '30')
      await user.click(screen.getByRole('button', { name: 'Save' }))
      await waitFor(() => expect(screen.getByTestId('popularity-value')).toHaveTextContent('30'))
      expect(api.setMemePopularity).toHaveBeenCalledTimes(1)
      expect(api.setMemePopularity).toHaveBeenCalledWith('a', 30)
      expect(api.getMemePopularity).toHaveBeenCalledTimes(1)
      expect(screen.getByText('Manual adjustment: +28')).toBeInTheDocument()
      expect(screen.getByRole('spinbutton')).toHaveValue(30)
    })

    it('renders a negative adjustment with a minus sign', async () => {
      await renderBlock({ popularity: 1, status: 'normal', breakdown: [{ source: 'own', count: 5 }, { source: 'adjustment', count: -4 }] })
      expect(screen.getByText('Manual adjustment: -4')).toBeInTheDocument()
    })

    it('disables Save while a save is in flight', async () => {
      const user = userEvent.setup()
      await renderBlock(base, vi.fn().mockReturnValue(new Promise(() => {})))
      await typeValue(user, '30')
      await user.click(screen.getByRole('button', { name: 'Save' }))
      expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
    })

    it('keeps the old number and shows an inline alert when the save is rejected', async () => {
      const user = userEvent.setup()
      const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {})
      await renderBlock(base, vi.fn().mockRejectedValue(new Error('409')))
      await typeValue(user, '30')
      await user.click(screen.getByRole('button', { name: 'Save' }))
      expect(await screen.findByRole('alert')).toBeInTheDocument()
      expect(screen.getByTestId('popularity-value')).toHaveTextContent('2')
      expect(alertSpy).not.toHaveBeenCalled()
    })
  })
})
