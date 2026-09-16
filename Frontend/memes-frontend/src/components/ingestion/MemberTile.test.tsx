import { render, screen } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import { MemberTile } from './MemberTile'
import { makeMockApi } from '../../test/mockApi'
import type { IngestionClusterMember } from '../../types/generated/all'

const baseMember: IngestionClusterMember = {
  image_id: 'a', filename: 'a.jpg', status: 'pending', ocr_text: null,
}

function renderTile(member: IngestionClusterMember) {
  return render(
    <MemberTile
      memesApi={makeMockApi()} member={member} edgeSummary={null}
      decision={undefined} onDecide={vi.fn()} onPeek={vi.fn()}
    />
  )
}

describe('MemberTile', () => {
  it('shows a Text-heavy chip when the member is classified text_heavy', () => {
    renderTile({ ...baseMember, text_heavy: true })
    expect(screen.getByText('Text-heavy')).toBeInTheDocument()
  })

  it('shows no chip when text_heavy is false', () => {
    renderTile({ ...baseMember, text_heavy: false })
    expect(screen.queryByText('Text-heavy')).toBeNull()
  })

  it('shows no chip when text_heavy is absent (not yet classified)', () => {
    renderTile(baseMember)
    expect(screen.queryByText('Text-heavy')).toBeNull()
  })
})
