import { render, screen, fireEvent } from '@testing-library/react'
import { Modal } from './Modal'

describe('Modal', () => {
  it('renders children', () => {
    render(<Modal onClose={() => {}}><p>Hello modal</p></Modal>)
    expect(screen.getByText('Hello modal')).toBeInTheDocument()
  })

  it('renders provided title', () => {
    render(<Modal title="My Title" onClose={() => {}}><span /></Modal>)
    expect(screen.getByText('My Title')).toBeInTheDocument()
  })

  it('falls back to "Details" when no title is provided', () => {
    render(<Modal onClose={() => {}}><span /></Modal>)
    expect(screen.getByText('Details')).toBeInTheDocument()
  })

  it('calls onClose when Escape key is pressed', () => {
    const onClose = vi.fn()
    render(<Modal onClose={onClose}><span /></Modal>)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledOnce()
  })

  it('calls onClose when backdrop is clicked', () => {
    const onClose = vi.fn()
    render(<Modal title="Test" onClose={onClose}><span /></Modal>)
    const backdrop = document.querySelector('.fixed.inset-0')!
    fireEvent.click(backdrop)
    expect(onClose).toHaveBeenCalledOnce()
  })

  it('does not call onClose when inner panel is clicked', () => {
    const onClose = vi.fn()
    render(<Modal title="Test" onClose={onClose}><p>Content</p></Modal>)
    fireEvent.click(screen.getByText('Content'))
    expect(onClose).not.toHaveBeenCalled()
  })

  it('calls onClose when close button is clicked', () => {
    const onClose = vi.fn()
    render(<Modal onClose={onClose}><span /></Modal>)
    fireEvent.click(screen.getByText('✕'))
    expect(onClose).toHaveBeenCalledOnce()
  })

  it('uses the scrim token for the backdrop', () => {
    render(<Modal onClose={() => {}}><span /></Modal>)
    const backdrop = document.querySelector('.fixed.inset-0')!
    expect(backdrop).toHaveClass('bg-scrim')
    expect(backdrop.className).not.toContain('bg-opacity-60')
  })

  it('locks body scroll when open', () => {
    render(<Modal onClose={() => {}}><span /></Modal>)
    expect(document.body.style.overflow).toBe('hidden')
  })

  it('restores body scroll on unmount', () => {
    const { unmount } = render(<Modal onClose={() => {}}><span /></Modal>)
    unmount()
    expect(document.body.style.overflow).toBe('auto')
  })

  it('keeps the default container and a visible heading by default', () => {
    render(<Modal title="T" onClose={() => {}}><span /></Modal>)
    const panel = screen.getByRole('heading', { name: 'T' }).closest('.bg-white')!
    expect(panel).toHaveClass('max-w-5xl')
    expect(screen.getByRole('heading', { name: 'T' })).not.toHaveClass('sr-only')
  })

  it('size="wide" applies the wide container class', () => {
    render(<Modal title="T" size="wide" onClose={() => {}}><span /></Modal>)
    const panel = screen.getByRole('heading', { name: 'T' }).closest('.bg-white')!
    expect(panel).toHaveClass('max-w-7xl')
    expect(panel).not.toHaveClass('max-w-5xl')
  })

  it('titleHidden keeps the heading for screen readers and the close button', () => {
    const onClose = vi.fn()
    render(<Modal title="T" titleHidden onClose={onClose}><span /></Modal>)
    expect(screen.getByRole('heading', { name: 'T' })).toHaveClass('sr-only')
    fireEvent.click(screen.getByText('✕'))
    expect(onClose).toHaveBeenCalledOnce()
  })
})
