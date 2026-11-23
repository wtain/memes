import { render, screen, fireEvent, act } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { MemeDetailsModal } from './MemeDetailsModal'
import { makeMockApi, DEFAULT_MOCK_MEME } from '../test/mockApi'
import { useFeature } from '../features/useFeature'
import type { Meme } from '../types/generated/all'

vi.mock('../features/useFeature', async importOriginal => {
  const actual = await importOriginal<typeof import('../features/useFeature')>()
  return { ...actual, useFeature: vi.fn(actual.useFeature) }
})

const prevMeme: Meme = { ...DEFAULT_MOCK_MEME, id: 'meme-prev' }
const nextMeme: Meme = { ...DEFAULT_MOCK_MEME, id: 'meme-next' }

function setup(neighbors?: Parameters<typeof MemeDetailsModal>[0]['neighbors'], onClose = vi.fn()) {
  const api = makeMockApi({
    getImageUrl: vi.fn().mockImplementation((m: Meme) => `http://example.com/img/${m.id}`),
  })
  render(
    <MemoryRouter>
      <MemeDetailsModal meme={DEFAULT_MOCK_MEME} memesApi={api} onClose={onClose} neighbors={neighbors} />
    </MemoryRouter>
  )
  return { api, onClose }
}

function callbacks() {
  return { onPrev: vi.fn(), onNext: vi.fn() }
}

afterEach(() => {
  vi.mocked(useFeature).mockReset()
  vi.restoreAllMocks()
})

describe('MemeDetailsModal neighbors', () => {
  it('ArrowRight calls onNext and ArrowLeft calls onPrev', () => {
    const cb = callbacks()
    setup({ prev: prevMeme, next: nextMeme, ...cb })
    fireEvent.keyDown(document.body, { key: 'ArrowRight' })
    expect(cb.onNext).toHaveBeenCalledTimes(1)
    expect(cb.onPrev).not.toHaveBeenCalled()
    fireEvent.keyDown(document.body, { key: 'ArrowLeft' })
    expect(cb.onPrev).toHaveBeenCalledTimes(1)
  })

  it('prevents the default only when it acts', () => {
    setup({ onNext: vi.fn() })
    expect(fireEvent.keyDown(document.body, { key: 'ArrowRight' })).toBe(false)
    expect(fireEvent.keyDown(document.body, { key: 'ArrowLeft' })).toBe(true)
    expect(fireEvent.keyDown(document.body, { key: 'a' })).toBe(true)
  })

  it('arrows are ignored while focus is in a textarea (Curate tab)', async () => {
    vi.mocked(useFeature).mockImplementation(flag => flag === 'curator')
    const cb = callbacks()
    setup({ ...cb })
    fireEvent.click(screen.getByRole('tab', { name: 'Curate' }))
    const note = screen.getByLabelText('Description note')
    note.focus()
    fireEvent.keyDown(note, { key: 'ArrowRight' })
    fireEvent.keyDown(note, { key: 'ArrowLeft' })
    expect(cb.onNext).not.toHaveBeenCalled()
    expect(cb.onPrev).not.toHaveBeenCalled()
  })

  it('arrows are ignored in an input, a select and a contenteditable element', () => {
    const cb = callbacks()
    setup({ ...cb })
    const input = document.body.appendChild(document.createElement('input'))
    const select = document.body.appendChild(document.createElement('select'))
    const editable = document.body.appendChild(document.createElement('div'))
    editable.setAttribute('contenteditable', 'true')
    const inner = editable.appendChild(document.createElement('span'))
    for (const el of [input, select, inner]) {
      fireEvent.keyDown(el, { key: 'ArrowRight' })
      fireEvent.keyDown(el, { key: 'ArrowLeft' })
    }
    expect(cb.onNext).not.toHaveBeenCalled()
    expect(cb.onPrev).not.toHaveBeenCalled()
    input.remove(); select.remove(); editable.remove()
  })

  it('arrows on a tab move between tabs and do not step', () => {
    const cb = callbacks()
    setup({ ...cb })
    const tab = screen.getAllByRole('tab')[0]
    tab.focus()
    fireEvent.keyDown(tab, { key: 'ArrowRight' })
    fireEvent.keyDown(tab, { key: 'ArrowLeft' })
    expect(cb.onNext).not.toHaveBeenCalled()
    expect(cb.onPrev).not.toHaveBeenCalled()
  })

  it.each(['ctrlKey', 'metaKey', 'altKey'])('arrows are ignored with %s held', mod => {
    const cb = callbacks()
    setup({ ...cb })
    fireEvent.keyDown(document.body, { key: 'ArrowRight', [mod]: true })
    fireEvent.keyDown(document.body, { key: 'ArrowLeft', [mod]: true })
    expect(cb.onNext).not.toHaveBeenCalled()
    expect(cb.onPrev).not.toHaveBeenCalled()
  })

  it('Previous and Next buttons call the callbacks', () => {
    const cb = callbacks()
    setup({ ...cb })
    fireEvent.click(screen.getByRole('button', { name: 'Next meme' }))
    fireEvent.click(screen.getByRole('button', { name: 'Previous meme' }))
    expect(cb.onNext).toHaveBeenCalledTimes(1)
    expect(cb.onPrev).toHaveBeenCalledTimes(1)
  })

  it('a button is disabled when its callback is missing', () => {
    setup({ onNext: vi.fn() })
    expect(screen.getByRole('button', { name: 'Previous meme' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Next meme' })).toBeEnabled()
  })

  it('no neighbors prop shows no buttons and ignores arrows', () => {
    const { onClose } = setup(undefined)
    expect(screen.queryByRole('button', { name: 'Previous meme' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Next meme' })).not.toBeInTheDocument()
    expect(fireEvent.keyDown(document.body, { key: 'ArrowRight' })).toBe(true)
    expect(onClose).not.toHaveBeenCalled()
  })

  it('preloads the next and previous images', () => {
    const srcs: string[] = []
    class FakeImage {
      set src(value: string) { srcs.push(value) }
    }
    vi.stubGlobal('Image', FakeImage)
    try {
      setup({ prev: prevMeme, next: nextMeme, ...callbacks() })
      expect(srcs).toContain('http://example.com/img/meme-next')
      expect(srcs).toContain('http://example.com/img/meme-prev')
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('Escape still closes', async () => {
    const { onClose } = setup({ ...callbacks() })
    await act(async () => { fireEvent.keyDown(document, { key: 'Escape' }) })
    expect(onClose).toHaveBeenCalled()
  })
})
