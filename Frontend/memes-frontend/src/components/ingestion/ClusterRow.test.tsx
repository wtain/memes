import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi } from 'vitest'
import { ClusterRow } from './ClusterRow'
import { makeMockApi } from '../../test/mockApi'
import type { IngestionCluster, IngestionClusterMember } from '../../types/generated/all'

const cluster: IngestionCluster = {
  members: [
    { image_id: 'a', filename: 'a.jpg', status: 'pending', ocr_text: 'Не смешно, совсем не смешно, длинный текст который раньше обрезался' },
    { image_id: 'b', filename: 'b.jpg', status: 'active', ocr_text: 'Не смешно' },
  ],
  edges: [{ image_id1: 'a', image_id2: 'b', distance: 0.04, match_source: 'clip' }],
  total_members: 2,
}

function renderRow(props: Partial<Parameters<typeof ClusterRow>[0]> = {}) {
  return render(
    <ClusterRow
      memesApi={makeMockApi()}
      cluster={cluster}
      decisions={{}}
      onDecide={vi.fn()}
      onSubmit={vi.fn()}
      submitting={false}
      expanded={false}
      onToggleExpand={vi.fn()}
      onPeek={vi.fn()}
      {...props}
    />
  )
}

describe('ClusterRow', () => {
  it('renders every member image uncropped (object-contain) with full OCR text, not clamped', () => {
    renderRow()
    const imgs = screen.getAllByRole('img')
    expect(imgs).toHaveLength(2)
    imgs.forEach((img) => {
      expect(img.className).toContain('object-contain')
      expect(img.className).not.toContain('object-cover')
      expect(img).toHaveAttribute('loading', 'lazy')
      expect(img).toHaveAttribute('decoding', 'async')
    })
    expect(screen.getByText(/длинный текст который раньше обрезался/)).toBeInTheDocument()
    expect(document.querySelector('.line-clamp-3')).toBeNull()
  })

  it('lays members out in a wrap grid, never a horizontal scroll strip', () => {
    const { container } = renderRow()
    const grid = container.querySelector('.grid')
    expect(grid).not.toBeNull()
    expect(grid!.className).toContain('auto-fill')
    expect(container.querySelector('.overflow-x-auto')).toBeNull()
  })

  it('shows Keep/Reject only for pending members and calls onDecide', async () => {
    const onDecide = vi.fn()
    renderRow({ onDecide })
    expect(screen.getAllByRole('button', { name: /keep/i })).toHaveLength(1)
    await userEvent.click(screen.getByRole('button', { name: /reject/i }))
    expect(onDecide).toHaveBeenCalledWith('a', 'reject')
  })

  it('condenses a member\'s edge distances into one summary line', () => {
    renderRow()
    // both members sit on the single a<->b edge, so both tiles show the same one-line summary
    expect(screen.getAllByText(/0\.040 nearest · 1 pair/)).toHaveLength(2)
  })

  it('opens the peek modal when a member image is clicked', async () => {
    const onPeek = vi.fn()
    renderRow({ onPeek })
    await userEvent.click(screen.getAllByRole('button', { name: /open a\.jpg full size/i })[0])
    expect(onPeek).toHaveBeenCalledWith(cluster.members[0])
  })

  it('collapses a large cluster to the first 8 members behind a "show more" toggle', async () => {
    const big: IngestionCluster = {
      members: Array.from({ length: 12 }, (_, i): IngestionClusterMember => ({
        image_id: `m${i}`, filename: `m${i}.jpg`, status: 'pending', ocr_text: null,
      })),
      edges: [],
      total_members: 12,
    }
    const onToggleExpand = vi.fn()
    const { rerender } = render(
      <ClusterRow
        memesApi={makeMockApi()} cluster={big} decisions={{}} onDecide={vi.fn()}
        onSubmit={vi.fn()} submitting={false} expanded={false}
        onToggleExpand={onToggleExpand} onPeek={vi.fn()}
      />
    )
    expect(screen.getAllByRole('img')).toHaveLength(8)
    await userEvent.click(screen.getByRole('button', { name: /show 4 more/i }))
    expect(onToggleExpand).toHaveBeenCalled()

    rerender(
      <ClusterRow
        memesApi={makeMockApi()} cluster={big} decisions={{}} onDecide={vi.fn()}
        onSubmit={vi.fn()} submitting={false} expanded={true}
        onToggleExpand={onToggleExpand} onPeek={vi.fn()}
      />
    )
    expect(screen.getAllByRole('img')).toHaveLength(12)
    expect(screen.getByRole('button', { name: /show fewer/i })).toBeInTheDocument()
  })

  it('shows a "Showing N of M" notice when the cluster is capped', () => {
    const capped: IngestionCluster = {
      members: Array.from({ length: 8 }, (_, i): IngestionClusterMember => ({
        image_id: `c${i}`, filename: `c${i}.jpg`, status: 'pending', ocr_text: null,
      })),
      edges: [],
      total_members: 512,
    }
    renderRow({ cluster: capped })
    expect(screen.getByText(/showing 8 of 512/i)).toBeInTheDocument()
    expect(screen.getByText(/per-image review is coming/i)).toBeInTheDocument()
  })

  it('renders a capped cluster read-only: no Keep/Reject buttons, submit stays disabled', () => {
    const capped: IngestionCluster = {
      members: Array.from({ length: 8 }, (_, i): IngestionClusterMember => ({
        image_id: `c${i}`, filename: `c${i}.jpg`, status: 'pending', ocr_text: null,
      })),
      edges: [],
      total_members: 512,
    }
    renderRow({ cluster: capped })
    expect(screen.queryAllByRole('button', { name: /^(keep|reject)$/i })).toHaveLength(0)
    expect(screen.getByRole('button', { name: /submit decisions/i })).toBeDisabled()
  })

  it('still renders Keep/Reject for a normal (uncapped) cluster', () => {
    renderRow()
    expect(screen.queryAllByRole('button', { name: /^(keep|reject)$/i }).length).toBeGreaterThan(0)
  })

  it('shows no cap notice for a collapsed-but-not-capped cluster', () => {
    // 12 members collapse to 8 shown, but total_members === members.length -> nothing is
    // capped, so the notice must not gate on shown.length.
    const collapsed: IngestionCluster = {
      members: Array.from({ length: 12 }, (_, i): IngestionClusterMember => ({
        image_id: `n${i}`, filename: `n${i}.jpg`, status: 'pending', ocr_text: null,
      })),
      edges: [],
      total_members: 12,
    }
    renderRow({ cluster: collapsed })
    expect(screen.queryByText(/showing .* of /i)).toBeNull()
  })

  it('disables the per-cluster submit until a pending member has a decision', () => {
    const { rerender } = renderRow()
    expect(screen.getByRole('button', { name: /submit decisions/i })).toBeDisabled()
    rerender(
      <ClusterRow
        memesApi={makeMockApi()} cluster={cluster} decisions={{ a: 'keep' }} onDecide={vi.fn()}
        onSubmit={vi.fn()} submitting={false} expanded={false} onToggleExpand={vi.fn()} onPeek={vi.fn()}
      />
    )
    expect(screen.getByRole('button', { name: /submit decisions/i })).toBeEnabled()
  })
})
