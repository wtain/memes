import { render, screen, waitFor } from '@testing-library/react'
import BusinessMetricsPanel from './BusinessMetricsPanel'
import { makeMockApi } from '../test/mockApi'
import type { BatchMetricsItem, BatchMetricsResponse } from '../types/generated/all'

const ZERO = { counts: { proposed: 0, approved: 0, rejected: 0 }, metrics: { approve_rate: null, decided_share: null, pending: 0 } }

function makeItem(overrides: Partial<BatchMetricsItem> = {}): BatchMetricsItem {
  return {
    run_id: 'run-1',
    run_created_at: '2026-10-18T09:00:00Z',
    counts: { proposed: 4, approved: 1, rejected: 1 },
    metrics: { approve_rate: 0.5, decided_share: 0.5, pending: 2 },
    ...overrides,
  }
}

function makeResponse(overrides: Partial<BatchMetricsResponse> = {}): BatchMetricsResponse {
  return { process: 'recut', items: [makeItem()], unattributed: ZERO, ...overrides }
}

function renderPanel(getBatchMetrics: ReturnType<typeof vi.fn>) {
  const api = makeMockApi({ getBatchMetrics })
  render(<BusinessMetricsPanel memesApi={api} process="recut" title="Recut outcomes" emptyText="No recut proposals yet" />)
  return api
}

describe('BusinessMetricsPanel', () => {
  it('renders one row per run with counts and percentages', async () => {
    renderPanel(vi.fn().mockResolvedValue(makeResponse()))

    await waitFor(() => expect(screen.getByText('Recut outcomes')).toBeInTheDocument())
    expect(await screen.findAllByText('50.0%')).toHaveLength(2) // approve rate and decided share
    expect(screen.getByText('Approve rate')).toBeInTheDocument()
    expect(screen.getByText('Decided share')).toBeInTheDocument()
    expect(screen.getByText('4')).toBeInTheDocument() // proposed
    expect(screen.getByText('2')).toBeInTheDocument() // pending, shown as a count not a percentage
    expect(screen.getByText(/not counted as rejections/i)).toBeInTheDocument()
  })

  it('shows the adjusted count and its share next to the other outcomes', async () => {
    const item = makeItem({
      counts: { proposed: 4, adjusted: 1, approved: 1, rejected: 1 },
      metrics: { approve_rate: 0.5, decided_share: 0.5, pending: 2, adjusted_share: 0.25 },
    })
    renderPanel(vi.fn().mockResolvedValue(makeResponse({ items: [item] })))

    expect(await screen.findByText('Adjusted')).toBeInTheDocument()
    expect(screen.getByText('Adjusted share')).toBeInTheDocument()
    expect(screen.getByText('25.0%')).toBeInTheDocument()
  })

  it('shows a dash for a missing rate', async () => {
    renderPanel(vi.fn().mockResolvedValue(makeResponse({
      items: [makeItem({
        counts: { proposed: 1, approved: 0, rejected: 0 },
        metrics: { approve_rate: null, decided_share: 0, pending: 1 },
      })],
    })))

    expect(await screen.findByText('-')).toBeInTheDocument()
    expect(screen.getByText('0.0%')).toBeInTheDocument()
  })

  it('shows an unattributed row when events lost their run', async () => {
    renderPanel(vi.fn().mockResolvedValue(makeResponse({
      items: [],
      unattributed: { counts: { proposed: 3, approved: 0, rejected: 0 }, metrics: { approve_rate: null, decided_share: 0, pending: 3 } },
    })))

    expect(await screen.findByText('Unattributed')).toBeInTheDocument()
  })

  it('shows the empty text when there are no events', async () => {
    renderPanel(vi.fn().mockResolvedValue(makeResponse({ items: [] })))

    expect(await screen.findByText('No recut proposals yet')).toBeInTheDocument()
  })

  it('shows an error when the fetch fails', async () => {
    renderPanel(vi.fn().mockRejectedValue(new Error('boom')))

    expect(await screen.findByText('boom')).toBeInTheDocument()
  })

  it('asks for the process it was given', async () => {
    const getBatchMetrics = vi.fn().mockResolvedValue(makeResponse())
    renderPanel(getBatchMetrics)

    await waitFor(() => expect(getBatchMetrics).toHaveBeenCalledWith('recut'))
  })
})
