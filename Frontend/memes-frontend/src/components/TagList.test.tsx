import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { TagList } from './TagList'
import type { MemeTag } from '../types/generated/all'

describe('TagList', () => {
  it('renders nothing for an empty tag array', () => {
    const { container } = render(<TagList tags={[]} />)
    expect(container.firstChild).toBeEmptyDOMElement()
  })

  it('filters out tags with score at or below 0.3', () => {
    const tags: MemeTag[] = [
      { name: 'visible', score: 0.5, source: 'clip' },
      { name: 'at-threshold', score: 0.3, source: 'clip' },
      { name: 'below-threshold', score: 0.1, source: 'clip' },
    ]
    render(<TagList tags={tags} />)
    expect(screen.queryByText(/#visible/)).toBeInTheDocument()
    expect(screen.queryByText(/#at-threshold/)).not.toBeInTheDocument()
    expect(screen.queryByText(/#below-threshold/)).not.toBeInTheDocument()
  })

  it('filters out tags with undefined score', () => {
    const tags: MemeTag[] = [{ name: 'no-score', source: 'manual' }]
    render(<TagList tags={tags} />)
    expect(screen.queryByText(/#no-score/)).not.toBeInTheDocument()
  })

  it('shows tag name and source in label', () => {
    const tags: MemeTag[] = [{ name: 'cat', source: 'clip', score: 0.9 }]
    render(<TagList tags={tags} />)
    expect(screen.getByText('#cat (clip)')).toBeInTheDocument()
  })

  it('renders multiple qualifying tags', () => {
    const tags: MemeTag[] = [
      { name: 'cats', source: 'ai', score: 0.8 },
      { name: 'dogs', source: 'ai', score: 0.6 },
    ]
    render(<TagList tags={tags} />)
    expect(screen.getByText('#cats (ai)')).toBeInTheDocument()
    expect(screen.getByText('#dogs (ai)')).toBeInTheDocument()
  })

  it('shows all tags with no toggle button when there are 3 or fewer qualifying tags', () => {
    const tags: MemeTag[] = [
      { name: 'one', source: 'ai', score: 0.8 },
      { name: 'two', source: 'ai', score: 0.7 },
      { name: 'three', source: 'ai', score: 0.6 },
    ]
    render(<TagList tags={tags} />)
    expect(screen.getByText('#one (ai)')).toBeInTheDocument()
    expect(screen.getByText('#two (ai)')).toBeInTheDocument()
    expect(screen.getByText('#three (ai)')).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('shows only the first 3 qualifying tags plus a toggle when there are more than 3', () => {
    const tags: MemeTag[] = [
      { name: 'one', source: 'ai', score: 0.8 },
      { name: 'two', source: 'ai', score: 0.7 },
      { name: 'three', source: 'ai', score: 0.6 },
      { name: 'four', source: 'ai', score: 0.5 },
      { name: 'five', source: 'ai', score: 0.4 },
    ]
    render(<TagList tags={tags} />)
    expect(screen.getByText('#one (ai)')).toBeInTheDocument()
    expect(screen.getByText('#two (ai)')).toBeInTheDocument()
    expect(screen.getByText('#three (ai)')).toBeInTheDocument()
    expect(screen.queryByText('#four (ai)')).not.toBeInTheDocument()
    expect(screen.queryByText('#five (ai)')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '+2 more' })).toBeInTheDocument()
  })

  it('reveals the remaining tags when the toggle is clicked', async () => {
    const user = userEvent.setup()
    const tags: MemeTag[] = [
      { name: 'one', source: 'ai', score: 0.8 },
      { name: 'two', source: 'ai', score: 0.7 },
      { name: 'three', source: 'ai', score: 0.6 },
      { name: 'four', source: 'ai', score: 0.5 },
      { name: 'five', source: 'ai', score: 0.4 },
    ]
    render(<TagList tags={tags} />)
    await user.click(screen.getByRole('button', { name: '+2 more' }))
    expect(screen.getByText('#four (ai)')).toBeInTheDocument()
    expect(screen.getByText('#five (ai)')).toBeInTheDocument()
  })

  it('collapses back to 3 tags when the toggle is clicked again', async () => {
    const user = userEvent.setup()
    const tags: MemeTag[] = [
      { name: 'one', source: 'ai', score: 0.8 },
      { name: 'two', source: 'ai', score: 0.7 },
      { name: 'three', source: 'ai', score: 0.6 },
      { name: 'four', source: 'ai', score: 0.5 },
      { name: 'five', source: 'ai', score: 0.4 },
    ]
    render(<TagList tags={tags} />)
    await user.click(screen.getByRole('button', { name: '+2 more' }))
    await user.click(screen.getByRole('button', { name: 'show less' }))
    expect(screen.queryByText('#four (ai)')).not.toBeInTheDocument()
    expect(screen.queryByText('#five (ai)')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '+2 more' })).toBeInTheDocument()
  })
})
