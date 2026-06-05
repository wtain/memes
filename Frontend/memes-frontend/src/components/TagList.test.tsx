import { render, screen } from '@testing-library/react'
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
})
