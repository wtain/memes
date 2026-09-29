export function formatUpdatedAgo(
  computedAt: string | null | undefined,
  now: number = Date.now(),
): string | null {
  if (!computedAt) return null
  const then = Date.parse(computedAt)
  if (Number.isNaN(then)) return null

  const minutes = Math.floor((now - then) / 60_000)
  if (minutes < 1) return 'Updated just now'
  if (minutes < 60) return `Updated ${minutes} min ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `Updated ${hours} h ago`
  return `Updated ${Math.floor(hours / 24)} d ago`
}
