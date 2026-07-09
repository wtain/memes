import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import MemeCard from './MemeCard'
import { makeMockApi } from '../test/mockApi'
import type { Meme } from '../types/generated/all'

const mockMeme: Meme = {
  id: 'meme-1',
  imageUrl: '/images/meme-1.jpg',
  text: ['Hello World', 'Second line'],
  tags: [{ name: 'funny', source: 'clip', score: 0.9 }],
  flagged: false,
}

const cardApi = () => makeMockApi({ getImageUrl: vi.fn().mockReturnValue('http://example.com/meme-1.jpg') })

describe('MemeCard', () => {
  it('renders the meme image with URL from api.getImageUrl', () => {
    const api = cardApi()
    render(<MemeCard meme={mockMeme} memesApi={api} />)
    const img = screen.getByRole('img', { name: 'meme-1' })
    expect(img).toHaveAttribute('src', 'http://example.com/meme-1.jpg')
    expect(api.getImageUrl).toHaveBeenCalledWith(mockMeme)
  })

  it('shows OCR text overlay on OCR button click', () => {
    render(<MemeCard meme={mockMeme} memesApi={cardApi()} />)
    expect(screen.queryByText('Hello World')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'OCR' }))
    expect(screen.getByText('Hello World')).toBeInTheDocument()
    expect(screen.getByText('Second line')).toBeInTheDocument()
  })

  it('hides OCR text overlay when overlay is clicked', () => {
    render(<MemeCard meme={mockMeme} memesApi={cardApi()} />)
    fireEvent.click(screen.getByRole('button', { name: 'OCR' }))
    expect(screen.getByText('Hello World')).toBeInTheDocument()
    fireEvent.click(screen.getByText('Hello World'))
    expect(screen.queryByText('Hello World')).not.toBeInTheDocument()
  })

  it('does not show OCR button when meme has no text', () => {
    render(<MemeCard meme={{ ...mockMeme, text: [] }} memesApi={cardApi()} />)
    expect(screen.queryByRole('button', { name: 'OCR' })).not.toBeInTheDocument()
  })

  it('checkbox is unchecked when meme is not flagged', () => {
    render(<MemeCard meme={{ ...mockMeme, flagged: false }} memesApi={cardApi()} />)
    expect(screen.getByRole('checkbox')).not.toBeChecked()
  })

  it('checkbox is checked when meme is already flagged', () => {
    render(<MemeCard meme={{ ...mockMeme, flagged: true }} memesApi={cardApi()} />)
    expect(screen.getByRole('checkbox')).toBeChecked()
  })

  it('calls markImageIsFlagged when unchecked checkbox is clicked', async () => {
    const api = cardApi()
    render(<MemeCard meme={{ ...mockMeme, flagged: false }} memesApi={api} />)
    fireEvent.click(screen.getByRole('checkbox'))
    await waitFor(() => {
      expect(api.markImageIsFlagged).toHaveBeenCalledWith('meme-1')
    })
  })

  it('calls unmarkImageIsFlagged when checked checkbox is clicked', async () => {
    const api = cardApi()
    render(<MemeCard meme={{ ...mockMeme, flagged: true }} memesApi={api} />)
    fireEvent.click(screen.getByRole('checkbox'))
    await waitFor(() => {
      expect(api.unmarkImageIsFlagged).toHaveBeenCalledWith('meme-1')
    })
  })

  it('calls onClick when the image area is clicked', () => {
    const onClick = vi.fn()
    render(<MemeCard meme={mockMeme} memesApi={cardApi()} onClick={onClick} />)
    fireEvent.click(screen.getByRole('img'))
    expect(onClick).toHaveBeenCalledOnce()
  })
})
