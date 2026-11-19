import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import MemeCard from './MemeCard'
import { FeaturesContext } from '../features/useFeature'
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

const prod = (ui: React.ReactElement) => (
  <FeaturesContext.Provider value={{ stage: 'prod', capabilities: null }}>
    <MemoryRouter>{ui}</MemoryRouter>
  </FeaturesContext.Provider>
)
const dev = (ui: React.ReactElement) => (
  <FeaturesContext.Provider value={{ stage: 'dev', capabilities: null }}>
    <MemoryRouter>{ui}</MemoryRouter>
  </FeaturesContext.Provider>
)

describe('MemeCard', () => {
  it('renders the meme image with URL from api.getImageUrl', () => {
    const api = cardApi()
    render(<MemeCard meme={mockMeme} memesApi={api} />)
    const img = screen.getByRole('img', { name: 'Meme' })
    expect(img).toHaveAttribute('src', 'http://example.com/meme-1.jpg')
    expect(api.getImageUrl).toHaveBeenCalledWith(mockMeme)
  })

  it('describes the image with a visually hidden OCR excerpt', () => {
    render(<MemeCard meme={mockMeme} memesApi={cardApi()} />)
    const img = screen.getByRole('img', { name: 'Meme' })
    const id = img.getAttribute('aria-describedby')!
    expect(id).toBeTruthy()
    const target = document.getElementById(id)!
    expect(target).toHaveClass('sr-only')
    expect(target.textContent).toContain('Hello World')
  })

  it('cuts the description at 120 characters', () => {
    render(<MemeCard meme={{ ...mockMeme, text: ['x'.repeat(300)] }} memesApi={cardApi()} />)
    const id = screen.getByRole('img', { name: 'Meme' }).getAttribute('aria-describedby')!
    expect(document.getElementById(id)!.textContent!.length).toBeLessThanOrEqual(120)
  })

  it.each([[[]], [undefined]])('has no description and keeps alt "Meme" when text is %j', text => {
    render(<MemeCard meme={{ ...mockMeme, text }} memesApi={cardApi()} />)
    const img = screen.getByRole('img', { name: 'Meme' })
    expect(img).not.toHaveAttribute('aria-describedby')
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
    fireEvent.click(screen.getByText('Hello World'))
    expect(screen.queryByText('Hello World')).not.toBeInTheDocument()
  })

  it('closes the OCR overlay with Enter and Escape and ignores image clicks while open', () => {
    const onClick = vi.fn()
    render(<MemeCard meme={mockMeme} memesApi={cardApi()} onClick={onClick} />)
    fireEvent.click(screen.getByRole('button', { name: 'OCR' }))
    fireEvent.click(screen.getByRole('img'))
    expect(onClick).not.toHaveBeenCalled()
    fireEvent.keyDown(screen.getByText('Hello World').closest('[role="button"]')!, { key: 'Enter' })
    expect(screen.queryByText('Hello World')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'OCR' }))
    fireEvent.keyDown(screen.getByText('Hello World').closest('[role="button"]')!, { key: 'Escape' })
    expect(screen.queryByText('Hello World')).not.toBeInTheDocument()
  })

  it('does not show OCR button when meme has no text', () => {
    render(<MemeCard meme={{ ...mockMeme, text: [] }} memesApi={cardApi()} />)
    expect(screen.queryByRole('button', { name: 'OCR' })).not.toBeInTheDocument()
  })

  it('flag toggle is not pressed when meme is not flagged', () => {
    render(<MemeCard meme={{ ...mockMeme, flagged: false }} memesApi={cardApi()} />)
    expect(screen.getByRole('button', { name: 'Flagged' })).toHaveAttribute('aria-pressed', 'false')
  })

  it('flag toggle is pressed when meme is already flagged', () => {
    render(<MemeCard meme={{ ...mockMeme, flagged: true }} memesApi={cardApi()} />)
    expect(screen.getByRole('button', { name: 'Flagged' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('calls markImageIsFlagged when the unpressed toggle is clicked', async () => {
    const api = cardApi()
    render(<MemeCard meme={{ ...mockMeme, flagged: false }} memesApi={api} />)
    fireEvent.click(screen.getByRole('button', { name: 'Flagged' }))
    await waitFor(() => {
      expect(api.markImageIsFlagged).toHaveBeenCalledWith('meme-1')
    })
    await waitFor(() => expect(screen.getByRole('button', { name: 'Flagged' })).toHaveAttribute('aria-pressed', 'true'))
  })

  it('calls unmarkImageIsFlagged when the pressed toggle is clicked', async () => {
    const api = cardApi()
    render(<MemeCard meme={{ ...mockMeme, flagged: true }} memesApi={api} />)
    fireEvent.click(screen.getByRole('button', { name: 'Flagged' }))
    await waitFor(() => {
      expect(api.unmarkImageIsFlagged).toHaveBeenCalledWith('meme-1')
    })
  })

  it('calls onClick when the image area is clicked and prevents navigation', () => {
    const onClick = vi.fn()
    render(<MemeCard meme={mockMeme} memesApi={cardApi()} onClick={onClick} />)
    const notPrevented = fireEvent.click(screen.getByRole('img'))
    expect(onClick).toHaveBeenCalledOnce()
    expect(notPrevented).toBe(false)
  })
})

describe('MemeCard link', () => {
  const link = () => screen.getByRole('img').closest('a')!

  it('wraps the image in a link to the meme page', () => {
    render(<MemeCard meme={mockMeme} memesApi={cardApi()} />)
    expect(link()).toHaveAttribute('href', '/memes/meme-1')
  })

  it('lets the href prop override the target', () => {
    render(<MemeCard meme={mockMeme} memesApi={cardApi()} href="/elsewhere" />)
    expect(link()).toHaveAttribute('href', '/elsewhere')
  })

  it.each([
    ['ctrlKey', { ctrlKey: true }],
    ['metaKey', { metaKey: true }],
    ['shiftKey', { shiftKey: true }],
    ['altKey', { altKey: true }],
    ['middle button', { button: 1 }],
  ])('leaves a %s click to the browser', (_n, init) => {
    const onClick = vi.fn()
    render(<MemeCard meme={mockMeme} memesApi={cardApi()} onClick={onClick} />)
    const notPrevented = fireEvent.click(screen.getByRole('img'), init)
    expect(onClick).not.toHaveBeenCalled()
    expect(notPrevented).toBe(true)
  })

  it('leaves a plain click to the browser when there is no onClick', () => {
    render(<MemeCard meme={mockMeme} memesApi={cardApi()} />)
    expect(fireEvent.click(screen.getByRole('img'))).toBe(true)
  })

  it('keeps the controls outside the link', () => {
    render(<MemeCard meme={mockMeme} memesApi={cardApi()} />)
    expect(link()).not.toContainElement(screen.getByRole('button', { name: 'OCR' }))
    expect(link()).not.toContainElement(screen.getByRole('button', { name: 'Flagged' }))
  })
})

describe('MemeCard curator gating and layout', () => {
  it('prod: no curator controls, no badge, no ring, no empty action bar', () => {
    render(prod(<MemeCard meme={{ ...mockMeme, flagged: true }} memesApi={cardApi()} comicsAction />))
    expect(screen.queryByRole('button', { name: 'Flagged' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Add to comics/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Curator actions' })).not.toBeInTheDocument()
    expect(screen.queryByText('Flagged')).not.toBeInTheDocument()
    expect(screen.getByTestId('card-root').className).not.toMatch(/ring-danger/)
    expect(screen.getByTestId('card-root').className).not.toMatch(/ring-2/)
  })

  it('dev: curator group is present and the comics button follows comicsAction', () => {
    const { rerender } = render(dev(<MemeCard meme={mockMeme} memesApi={cardApi()} />))
    expect(screen.getByRole('group', { name: 'Curator actions' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Add to comics' })).not.toBeInTheDocument()
    rerender(dev(<MemeCard meme={mockMeme} memesApi={cardApi()} comicsAction />))
    expect(screen.getByRole('button', { name: 'Add to comics' })).toBeInTheDocument()
  })

  it('dev + flagged: badge and danger ring, no black border', () => {
    render(<MemeCard meme={{ ...mockMeme, flagged: true }} memesApi={cardApi()} />)
    expect(screen.getByText('Flagged', { selector: 'span' })).toBeInTheDocument()
    const root = screen.getByTestId('card-root')
    expect(root.className).not.toMatch(/border-black/)
    // important modifiers so the danger ring beats the Card's hairline ring regardless of CSS order
    expect(root).toHaveClass('ring-2!', 'ring-danger!')
  })

  it('reveals the action bar on hover, focus and coarse pointers without removing it from the tab order', () => {
    render(<MemeCard meme={mockMeme} memesApi={cardApi()} />)
    const bar = screen.getByRole('group', { name: 'Curator actions' }).parentElement!
    expect(bar).toHaveClass('opacity-0', 'group-hover:opacity-100', 'group-focus-within:opacity-100', 'pointer-coarse:opacity-100')
    expect(screen.getByRole('button', { name: 'Flagged' })).not.toHaveAttribute('tabindex', '-1')
  })

  it('keeps the action bar visible while a comics status or error is shown', async () => {
    const { setWorkingComics } = await import('../comics/workingComics')
    setWorkingComics({ id: 'w', name: 'Work' })
    const api = makeMockApi({
      getImageUrl: vi.fn().mockReturnValue('x'),
      addComicsPanel: vi.fn().mockResolvedValue({}),
    })
    render(<FeaturesContext.Provider value={{ stage: 'dev', capabilities: null }}><MemoryRouter><MemeCard meme={mockMeme} memesApi={api} comicsAction /></MemoryRouter></FeaturesContext.Provider>)
    const bar = screen.getByRole('group', { name: 'Curator actions' }).parentElement!
    expect(bar).toHaveClass('has-[[data-keep-visible]]:opacity-100')
    expect(bar.querySelector('[data-keep-visible]')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Add to Work' }))
    const status = await screen.findByRole('status')
    expect(bar).toContainElement(status)
    expect(status).toHaveAttribute('data-keep-visible')
    expect(screen.getByRole('link', { name: 'Open editor' })).toBeInTheDocument()
  })

  it('always renders the tag footer, with and without tags', () => {
    const { rerender } = render(<MemeCard meme={mockMeme} memesApi={cardApi()} />)
    expect(screen.getByTestId('card-footer')).toBeInTheDocument()
    rerender(<MemeCard meme={{ ...mockMeme, tags: [] }} memesApi={cardApi()} />)
    expect(screen.getByTestId('card-footer')).toBeInTheDocument()
    rerender(<MemeCard meme={{ ...mockMeme, tags: [1, 2, 3, 4].map(n => ({ name: `t${n}`, source: 'clip', score: 0.9 })) }} memesApi={cardApi()} />)
    expect(screen.getByTestId('card-footer')).toBeInTheDocument()
  })

  it('renders no share action group yet', () => {
    render(<MemeCard meme={mockMeme} memesApi={cardApi()} />)
    expect(screen.queryByRole('group', { name: 'Share actions' })).not.toBeInTheDocument()
  })
})

describe('MemeCard selection (duplicates review)', () => {
  it('renders a selection checkbox only when selectable, and calls onToggleSelect', () => {
    const api = makeMockApi()
    const onToggleSelect = vi.fn()
    const meme = { id: 'a', imageUrl: '/a.jpg', text: [], tags: [] }
    render(<MemeCard meme={meme} memesApi={api} selectable selected={false} onToggleSelect={onToggleSelect} />)

    fireEvent.click(screen.getByRole('checkbox', { name: 'Select' }))

    expect(onToggleSelect).toHaveBeenCalledTimes(1)
  })

  it('does not render a selection checkbox when not selectable', () => {
    const api = makeMockApi()
    const meme = { id: 'a', imageUrl: '/a.jpg', text: [], tags: [] }
    render(<MemeCard meme={meme} memesApi={api} />)

    expect(screen.queryByRole('checkbox', { name: 'Select' })).not.toBeInTheDocument()
  })

  it('renders a keeper control only while selected, and calls onSetKeeper', () => {
    const api = makeMockApi()
    const onSetKeeper = vi.fn()
    const meme = { id: 'a', imageUrl: '/a.jpg', text: [], tags: [] }
    render(<MemeCard meme={meme} memesApi={api} selectable selected onToggleSelect={vi.fn()} isKeeper={false} onSetKeeper={onSetKeeper} />)

    fireEvent.click(screen.getByRole('radio', { name: 'Keeper' }))

    expect(onSetKeeper).toHaveBeenCalledTimes(1)
  })

  it('renders a similarity badge when a similarity score is provided', () => {
    const api = makeMockApi()
    const meme = { id: 'a', imageUrl: '/a.jpg', text: [], tags: [] }
    render(<MemeCard meme={meme} memesApi={api} similarity={0.82} />)

    expect(screen.getByText('Caption match: 82%')).toBeInTheDocument()
  })
})
