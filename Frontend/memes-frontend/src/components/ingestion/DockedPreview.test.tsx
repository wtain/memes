import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi } from 'vitest'
import { DockedPreview } from './DockedPreview'
import { makeMockApi } from '../../test/mockApi'
import type { IngestionClusterMember } from '../../types/generated/all'

const member: IngestionClusterMember = {
  image_id: 'a', filename: 'a.jpg', status: 'pending', ocr_text: 'полный текст OCR',
}

describe('DockedPreview', () => {
  it('renders nothing when member is null', () => {
    const { container } = render(<DockedPreview memesApi={makeMockApi()} member={null}
      decision={undefined} onDecide={vi.fn()} onMouseEnter={vi.fn()} onMouseLeave={vi.fn()} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('shows the same image URL as the tile (cache hit) plus full OCR and Keep/Reject', async () => {
    const api = makeMockApi()
    const onDecide = vi.fn()
    render(<DockedPreview memesApi={api} member={member}
      decision={undefined} onDecide={onDecide} onMouseEnter={vi.fn()} onMouseLeave={vi.fn()} />)
    expect(screen.getByRole('img')).toHaveAttribute('src', api.getImageUrlById('a'))
    expect(screen.getByText('полный текст OCR')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /reject/i }))
    expect(onDecide).toHaveBeenCalledWith('reject')
  })

  it('does not render Keep/Reject for a non-pending member', () => {
    render(<DockedPreview memesApi={makeMockApi()} member={{ ...member, status: 'active' }}
      decision={undefined} onDecide={vi.fn()} onMouseEnter={vi.fn()} onMouseLeave={vi.fn()} />)
    expect(screen.queryByRole('button', { name: /reject/i })).toBeNull()
  })
})
