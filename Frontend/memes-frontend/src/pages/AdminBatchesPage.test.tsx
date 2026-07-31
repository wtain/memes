import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import AdminBatchesPage from './AdminBatchesPage'
import { makeMockApi } from '../test/mockApi'
import type { RunStatusResponse, RunListResponse } from '../types/generated/all'

function makeRun(overrides: Partial<RunStatusResponse> = {}): RunStatusResponse {
  return {
    run_id: 'run-1',
    batch_name: 'trends_batch',
    trigger: 'manual',
    status: 'completed',
    created_at: '2026-07-31T00:00:00Z',
    completed_at: '2026-07-31T00:01:00Z',
    error: null,
    ...overrides,
  }
}

function makeList(items: RunStatusResponse[], total = items.length): RunListResponse {
  return { items, total }
}

describe('AdminBatchesPage', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('renders one row per admin batch and the run history table', async () => {
    const api = makeMockApi({
      listBatchRuns: vi.fn().mockResolvedValue(makeList([makeRun()])),
    })
    render(<AdminBatchesPage memesApi={api} />)

    // 'trends_batch' appears twice once the run history loads: once as the trigger row's
    // batch label, once as the history table's Batch cell for the seeded run.
    await waitFor(() => expect(screen.getAllByText('trends_batch')).toHaveLength(2))
    expect(screen.getByText('move_flagged')).toBeInTheDocument()
    expect(screen.getByText('unregister_deleted_images')).toBeInTheDocument()
  })

  it('requires a second click to trigger a batch run', async () => {
    const trigger = vi.fn().mockResolvedValue({ run_id: 'run-2', status: 'running' })
    const listBatchRuns = vi.fn().mockResolvedValue(makeList([]))
    const api = makeMockApi({ listBatchRuns, triggerBatchRun: trigger })
    const user = userEvent.setup()
    render(<AdminBatchesPage memesApi={api} />)

    await waitFor(() => expect(screen.getByText('trends_batch')).toBeInTheDocument())
    const runButtons = screen.getAllByText('Run')
    await user.click(runButtons[0])
    expect(trigger).not.toHaveBeenCalled()
    expect(screen.getByText('Confirm?')).toBeInTheDocument()

    await user.click(screen.getByText('Confirm?'))
    await waitFor(() => expect(trigger).toHaveBeenCalledWith('trends_batch'))
    await waitFor(() => expect(listBatchRuns).toHaveBeenCalledTimes(2)) // initial load + reload after trigger
  })

  it('cancels the first batch confirm when a different batch is clicked', async () => {
    const trigger = vi.fn().mockResolvedValue({ run_id: 'run-2', status: 'running' })
    const api = makeMockApi({
      listBatchRuns: vi.fn().mockResolvedValue(makeList([])),
      triggerBatchRun: trigger,
    })
    const user = userEvent.setup()
    render(<AdminBatchesPage memesApi={api} />)

    await waitFor(() => expect(screen.getByText('trends_batch')).toBeInTheDocument())
    const runButtons = screen.getAllByText('Run')
    await user.click(runButtons[0]) // trends_batch -> Confirm?
    await user.click(runButtons[1]) // move_flagged -> Confirm?, cancels trends_batch's

    expect(screen.getAllByText('Run')).toHaveLength(2) // trends_batch reverted, unregister still "Run"
    expect(screen.getAllByText('Confirm?')).toHaveLength(1)

    await user.click(screen.getByText('Confirm?'))
    await waitFor(() => expect(trigger).toHaveBeenCalledWith('move_flagged'))
    expect(trigger).not.toHaveBeenCalledWith('trends_batch')
  })

  it('reverts to "Run" if not confirmed within the timeout', async () => {
    vi.useFakeTimers()
    const api = makeMockApi({ listBatchRuns: vi.fn().mockResolvedValue(makeList([])) })
    render(<AdminBatchesPage memesApi={api} />)

    await act(async () => { await vi.advanceTimersByTimeAsync(0) }) // flush initial load
    fireEvent.click(screen.getAllByText('Run')[0])
    expect(screen.getByText('Confirm?')).toBeInTheDocument()

    await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
    expect(screen.queryByText('Confirm?')).not.toBeInTheDocument()
    expect(screen.getAllByText('Run')).toHaveLength(3)
  })

  it('shows an inline error for a 409 without affecting other rows', async () => {
    const trigger = vi.fn().mockRejectedValue(new Error('trends_batch is already running'))
    const api = makeMockApi({
      listBatchRuns: vi.fn().mockResolvedValue(makeList([])),
      triggerBatchRun: trigger,
    })
    const user = userEvent.setup()
    render(<AdminBatchesPage memesApi={api} />)

    await waitFor(() => expect(screen.getByText('trends_batch')).toBeInTheDocument())
    const runButtons = screen.getAllByText('Run')
    await user.click(runButtons[0])
    await user.click(screen.getByText('Confirm?'))

    await waitFor(() => expect(screen.getByText('trends_batch is already running')).toBeInTheDocument())
    expect(screen.getAllByText('Run')).toHaveLength(3) // move_flagged/unregister rows unaffected
  })

  it('polls the run list while a run is active, and stops once nothing is running', async () => {
    vi.useFakeTimers()
    const listBatchRuns = vi.fn()
      .mockResolvedValueOnce(makeList([makeRun({ status: 'running' })]))
      .mockResolvedValueOnce(makeList([makeRun({ status: 'completed' })]))
    const api = makeMockApi({ listBatchRuns })
    render(<AdminBatchesPage memesApi={api} />)

    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(listBatchRuns).toHaveBeenCalledTimes(1)

    await act(async () => { await vi.advanceTimersByTimeAsync(4000) })
    expect(listBatchRuns).toHaveBeenCalledTimes(2)

    await act(async () => { await vi.advanceTimersByTimeAsync(4000) })
    expect(listBatchRuns).toHaveBeenCalledTimes(2) // no more polling once status is completed
  })

  it('disables the Run button and shows "Triggering…" while the trigger request is in flight', async () => {
    let resolveTrigger: (value: { run_id: string; status: string }) => void = () => {}
    const trigger = vi.fn().mockReturnValue(new Promise((resolve) => { resolveTrigger = resolve }))
    const listBatchRuns = vi.fn().mockResolvedValue(makeList([]))
    const api = makeMockApi({ listBatchRuns, triggerBatchRun: trigger })
    const user = userEvent.setup()
    render(<AdminBatchesPage memesApi={api} />)

    await waitFor(() => expect(screen.getByText('trends_batch')).toBeInTheDocument())
    const runButtons = screen.getAllByText('Run')
    await user.click(runButtons[0])
    await user.click(screen.getByText('Confirm?'))

    await waitFor(() => expect(trigger).toHaveBeenCalledWith('trends_batch'))
    const triggeringButton = screen.getByText('Triggering…')
    expect(triggeringButton).toBeInTheDocument()
    expect(triggeringButton).toBeDisabled()
    // Other rows remain untouched.
    expect(screen.getAllByText('Run')).toHaveLength(2)

    await act(async () => {
      resolveTrigger({ run_id: 'run-2', status: 'running' })
      await Promise.resolve()
    })

    await waitFor(() => expect(screen.queryByText('Triggering…')).not.toBeInTheDocument())
    expect(screen.getAllByText('Run')).toHaveLength(3)
  })

  it('clears a stale trigger error once a fresh load shows the batch running', async () => {
    const trigger = vi.fn().mockRejectedValue(new Error('trends_batch is already running'))
    const listBatchRuns = vi.fn()
      .mockResolvedValueOnce(makeList([], 25)) // initial load; total=25 enables Next
      .mockResolvedValueOnce(makeList([makeRun({ status: 'running' })])) // reload triggered by paging
    const api = makeMockApi({ listBatchRuns, triggerBatchRun: trigger })
    const user = userEvent.setup()
    render(<AdminBatchesPage memesApi={api} />)

    await waitFor(() => expect(screen.getByText('trends_batch')).toBeInTheDocument())
    const runButtons = screen.getAllByText('Run')
    await user.click(runButtons[0])
    await user.click(screen.getByText('Confirm?'))

    await waitFor(() => expect(screen.getByText('trends_batch is already running')).toBeInTheDocument())

    // A later load (here: triggered by paging, standing in for "the poll picking up the
    // run that actually started") sees the batch running and should clear the stale
    // error automatically, without any new trigger attempt on that batch.
    await user.click(screen.getByText('Next'))

    await waitFor(() => expect(screen.queryByText('trends_batch is already running')).not.toBeInTheDocument())
    expect(trigger).toHaveBeenCalledTimes(1) // error cleared by load(), not by re-triggering
  })

  it('paginates using Prev/Next based on total', async () => {
    const listBatchRuns = vi.fn().mockResolvedValue(makeList([makeRun()], 25))
    const api = makeMockApi({ listBatchRuns })
    const user = userEvent.setup()
    render(<AdminBatchesPage memesApi={api} />)

    await waitFor(() => expect(screen.getByText('Page 1 of 2')).toBeInTheDocument())
    expect(screen.getByText('Prev')).toBeDisabled()

    await user.click(screen.getByText('Next'))
    await waitFor(() => expect(listBatchRuns).toHaveBeenCalledWith(20, 20))
    expect(screen.getByText('Next')).toBeDisabled()
  })
})
