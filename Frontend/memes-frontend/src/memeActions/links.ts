export function memePageUrl(id: string): string {
  return new URL(`/memes/${id}`, window.location.origin).href
}
