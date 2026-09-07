import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi } from 'vitest'
import { ClusterRow } from './ClusterRow'
import { makeMockApi } from '../../test/mockApi'
import type { IngestionCluster } from '../../types/generated/all'

const cluster: IngestionCluster = {
  members: [
    { image_id: 'a', filename: 'a.jpg', status: 'pending', ocr_text: 'Не смешно, совсем не смешно, длинный текст который раньше обрезался' },
    { image_id: 'b', filename: 'b.jpg', status: 'active', ocr_text: 'Не смешно' },
  ],
  edges: [{ image_id1: 'a', image_id2: 'b', distance: 0.04, match_source: 'clip' }],
}

describe('ClusterRow', () => {
  it('renders every member image uncropped (object-contain) with full OCR text, not clamped', () => {
    render(<ClusterRow memesApi={makeMockApi()} cluster={cluster} decisions={{}}
      onDecide={vi.fn()} onSubmit={vi.fn()} submitting={false}
      onHoverPreview={vi.fn()} onLeavePreview={vi.fn()} onPeek={vi.fn()} />)
    // MemberTile's thumbnail carries role="button" (it opens the peek modal) + an aria-label,
    // so it's queried by alt text rather than the img role.
    const imgs = screen.getAllByAltText(/\.jpg$/)
    expect(imgs).toHaveLength(2)
    imgs.forEach(img => {
      expect(img.className).toContain('object-contain')
      expect(img.className).not.toContain('object-cover')
      expect(img).toHaveAttribute('loading', 'lazy')
      expect(img).toHaveAttribute('decoding', 'async')
    })
    expect(screen.getByText(/длинный текст который раньше обрезался/)).toBeInTheDocument()
    expect(document.querySelector('.line-clamp-3')).toBeNull()
  })

  it('shows Keep/Reject only for pending members and calls onDecide', async () => {
    const onDecide = vi.fn()
    render(<ClusterRow memesApi={makeMockApi()} cluster={cluster} decisions={{}}
      onDecide={onDecide} onSubmit={vi.fn()} submitting={false}
      onHoverPreview={vi.fn()} onLeavePreview={vi.fn()} onPeek={vi.fn()} />)
    expect(screen.getAllByRole('button', { name: /keep/i })).toHaveLength(1)
    await userEvent.click(screen.getByRole('button', { name: /reject/i }))
    expect(onDecide).toHaveBeenCalledWith('a', 'reject')
  })

  it('fires onHoverPreview with the member on pointer enter', async () => {
    const onHoverPreview = vi.fn()
    render(<ClusterRow memesApi={makeMockApi()} cluster={cluster} decisions={{}}
      onDecide={vi.fn()} onSubmit={vi.fn()} submitting={false}
      onHoverPreview={onHoverPreview} onLeavePreview={vi.fn()} onPeek={vi.fn()} />)
    await userEvent.hover(screen.getAllByAltText(/\.jpg$/)[0])
    expect(onHoverPreview).toHaveBeenCalledWith(cluster.members[0])
  })

  it('disables the per-cluster submit until a pending member has a decision', () => {
    const { rerender } = render(<ClusterRow memesApi={makeMockApi()} cluster={cluster} decisions={{}}
      onDecide={vi.fn()} onSubmit={vi.fn()} submitting={false}
      onHoverPreview={vi.fn()} onLeavePreview={vi.fn()} onPeek={vi.fn()} />)
    expect(screen.getByRole('button', { name: /submit decisions/i })).toBeDisabled()
    rerender(<ClusterRow memesApi={makeMockApi()} cluster={cluster} decisions={{ a: 'keep' }}
      onDecide={vi.fn()} onSubmit={vi.fn()} submitting={false}
      onHoverPreview={vi.fn()} onLeavePreview={vi.fn()} onPeek={vi.fn()} />)
    expect(screen.getByRole('button', { name: /submit decisions/i })).toBeEnabled()
  })
})
