import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi } from 'vitest'
import { TierBReviewCard } from './TierBReviewCard'
import { makeMockApi } from '../../test/mockApi'
import type { IngestionTierBReviewItem } from '../../types/generated/all'

const item: IngestionTierBReviewItem = {
  image: { image_id: 's1', filename: 's1.jpg', status: 'pending', ocr_text: 'subject' },
  candidates: [
    { member: { image_id: 'c1', filename: 'c1.jpg', status: 'pending', ocr_text: null }, distance: 0.08, match_source: 'in_batch', distance_source: 'clip' },
    { member: { image_id: 'c2', filename: 'c2.jpg', status: 'active', ocr_text: null }, distance: 0.15, match_source: 'cross_corpus', distance_source: 'clip' },
  ],
  total_candidates: 2,
}

function renderCard(props: Partial<Parameters<typeof TierBReviewCard>[0]> = {}) {
  return render(
    <TierBReviewCard memesApi={makeMockApi()} item={item} decisions={{}} onDecide={vi.fn()}
      onSubmit={vi.fn()} submitting={false} expanded={false} onToggleExpand={vi.fn()} onPeek={vi.fn()} {...props} />
  )
}

describe('TierBReviewCard', () => {
  it('shows Keep/Reject on the subject and on a pending candidate, not on an active one', () => {
    renderCard()
    // subject + c1 (pending) are decidable -> 2 Reject buttons; c2 (active) is context-only
    expect(screen.getAllByRole('button', { name: /^reject$/i })).toHaveLength(2)
    expect(screen.getByText('s1.jpg')).toBeInTheDocument()
    expect(screen.getByText('c2.jpg')).toBeInTheDocument()
  })

  it('calls onDecide with the subject id and with a pending candidate id', async () => {
    const onDecide = vi.fn()
    renderCard({ onDecide })
    const rejects = screen.getAllByRole('button', { name: /^reject$/i })
    await userEvent.click(rejects[0])  // subject
    expect(onDecide).toHaveBeenCalledWith('s1', 'reject')
    await userEvent.click(rejects[1])  // c1
    expect(onDecide).toHaveBeenCalledWith('c1', 'reject')
  })

  it('shows a candidate distance/source line', () => {
    renderCard()
    expect(screen.getByText(/0\.080 · visual · in_batch/)).toBeInTheDocument()
  })

  it('shows a text-embedding label for an ocr_text-sourced candidate', () => {
    const c3 = {
      member: { image_id: 'c3', filename: 'c3.jpg', status: 'active', ocr_text: null },
      distance: 0.08,
      match_source: 'in_batch',
      distance_source: 'ocr_text',
    }
    renderCard({ item: { ...item, candidates: [...item.candidates, c3] } })
    expect(screen.getByText(/0\.080 · text · in_batch/)).toBeInTheDocument()
  })

  it('shows "Showing K of N" when total_candidates exceeds the returned list', () => {
    renderCard({ item: { ...item, total_candidates: 240 } })
    expect(screen.getByText(/showing 2 of 240/i)).toBeInTheDocument()
  })

  it('per-card submit is disabled until a decidable image has a decision', () => {
    const { rerender } = renderCard()
    expect(screen.getByRole('button', { name: /submit decisions/i })).toBeDisabled()
    rerender(<TierBReviewCard memesApi={makeMockApi()} item={item} decisions={{ s1: 'keep' }}
      onDecide={vi.fn()} onSubmit={vi.fn()} submitting={false} expanded={false}
      onToggleExpand={vi.fn()} onPeek={vi.fn()} />)
    expect(screen.getByRole('button', { name: /submit decisions/i })).toBeEnabled()
  })
})
