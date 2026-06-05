import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import MemeCard from './MemeCard'
import { makeMockApi } from '../test/mockApi'
import type { Meme } from '../types/generated/all'

const mockMeme: Meme = {
  id: 'meme-1',
  imageUrl: '/images/meme-1.jpg',
  text: ['Hello World', 'Second line'],
  tags: [{ name: 'funny', source: 'clip', score: 0.9 }],
  excluded: false,
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

  it('shows OCR text overlay on mouse enter', () => {
    render(<MemeCard meme={mockMeme} memesApi={cardApi()} />)
    expect(screen.queryByText('Hello World')).not.toBeInTheDocument()
    fireEvent.mouseEnter(screen.getByRole('img').parentElement!)
    expect(screen.getByText('Hello World')).toBeInTheDocument()
    expect(screen.getByText('Second line')).toBeInTheDocument()
  })

  it('hides OCR text overlay on mouse leave', () => {
    render(<MemeCard meme={mockMeme} memesApi={cardApi()} />)
    const imageWrap = screen.getByRole('img').parentElement!
    fireEvent.mouseEnter(imageWrap)
    fireEvent.mouseLeave(imageWrap)
    expect(screen.queryByText('Hello World')).not.toBeInTheDocument()
  })

  it('does not show OCR overlay when meme has no text', () => {
    render(<MemeCard meme={{ ...mockMeme, text: [] }} memesApi={cardApi()} />)
    fireEvent.mouseEnter(screen.getByRole('img').parentElement!)
    expect(screen.queryByText('OCR')).not.toBeInTheDocument()
  })

  it('checkbox is unchecked when meme is not excluded', () => {
    render(<MemeCard meme={{ ...mockMeme, excluded: false }} memesApi={cardApi()} />)
    expect(screen.getByRole('checkbox')).not.toBeChecked()
  })

  it('checkbox is checked when meme is already excluded', () => {
    render(<MemeCard meme={{ ...mockMeme, excluded: true }} memesApi={cardApi()} />)
    expect(screen.getByRole('checkbox')).toBeChecked()
  })

  it('calls markImageIsExcluded when unchecked checkbox is clicked', async () => {
    const api = cardApi()
    render(<MemeCard meme={{ ...mockMeme, excluded: false }} memesApi={api} />)
    fireEvent.click(screen.getByRole('checkbox'))
    await waitFor(() => {
      expect(api.markImageIsExcluded).toHaveBeenCalledWith('meme-1')
    })
  })

  it('calls unmarkImageIsExcluded when checked checkbox is clicked', async () => {
    const api = cardApi()
    render(<MemeCard meme={{ ...mockMeme, excluded: true }} memesApi={api} />)
    fireEvent.click(screen.getByRole('checkbox'))
    await waitFor(() => {
      expect(api.unmarkImageIsExcluded).toHaveBeenCalledWith('meme-1')
    })
  })

  it('calls onClick when the image area is clicked', () => {
    const onClick = vi.fn()
    render(<MemeCard meme={mockMeme} memesApi={cardApi()} onClick={onClick} />)
    fireEvent.click(screen.getByRole('img').parentElement!)
    expect(onClick).toHaveBeenCalledOnce()
  })
})
