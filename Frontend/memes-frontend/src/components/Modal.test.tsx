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

  it('locks body scroll when open', () => {
    render(<Modal onClose={() => {}}><span /></Modal>)
    expect(document.body.style.overflow).toBe('hidden')
  })

  it('restores body scroll on unmount', () => {
    const { unmount } = render(<Modal onClose={() => {}}><span /></Modal>)
    unmount()
    expect(document.body.style.overflow).toBe('auto')
  })
})
