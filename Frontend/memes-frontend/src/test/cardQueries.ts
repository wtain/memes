import { waitFor } from '@testing-library/react'

// MemeCard images are all named "Meme" (OCR text is only a description), so tests identify a
// card's image by the meme page link that wraps it.
const selector = (id: string) => `a[href="/memes/${id}"] img`

export function queryAllCardImgs(id: string): HTMLElement[] {
  return Array.from(document.querySelectorAll<HTMLElement>(selector(id)))
}

export function queryCardImg(id: string): HTMLElement | null {
  return queryAllCardImgs(id)[0] ?? null
}

export function getCardImg(id: string): HTMLElement {
  const el = queryCardImg(id)
  if (!el) throw new Error(`No MemeCard image for meme "${id}"`)
  return el
}

export function findCardImg(id: string): Promise<HTMLElement> {
  return waitFor(() => getCardImg(id))
}

export function findAllCardImgs(): Promise<HTMLElement[]> {
  return waitFor(() => getAllCardImgs())
}

export function getAllCardImgs(): HTMLElement[] {
  const all = Array.from(document.querySelectorAll<HTMLElement>('a[href^="/memes/"] img'))
  if (all.length === 0) throw new Error('No MemeCard images')
  return all
}
