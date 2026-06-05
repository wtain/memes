import { render, screen } from '@testing-library/react'
import { Tag } from './Tag'

describe('Tag', () => {
  it('renders label prefixed with #', () => {
    render(<Tag label="funny" />)
    expect(screen.getByText('#funny')).toBeInTheDocument()
  })

  it('renders as a span element', () => {
    render(<Tag label="cats" />)
    expect(screen.getByText('#cats').tagName).toBe('SPAN')
  })

  it('renders special characters in label', () => {
    render(<Tag label="top-10" />)
    expect(screen.getByText('#top-10')).toBeInTheDocument()
  })
})
