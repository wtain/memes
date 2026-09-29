import { formatUpdatedAgo } from './formatUpdatedAgo'

const NOW = Date.parse('2026-09-29T12:00:00Z')

describe('formatUpdatedAgo', () => {
  it('returns null when there is no timestamp', () => {
    expect(formatUpdatedAgo(null, NOW)).toBeNull()
    expect(formatUpdatedAgo(undefined, NOW)).toBeNull()
  })

  it('returns null for an unparseable timestamp', () => {
    expect(formatUpdatedAgo('not a date', NOW)).toBeNull()
  })

  it('says "just now" under a minute, and for clock skew into the future', () => {
    expect(formatUpdatedAgo('2026-09-29T11:59:30Z', NOW)).toBe('Updated just now')
    expect(formatUpdatedAgo('2026-09-29T12:05:00Z', NOW)).toBe('Updated just now')
  })

  it('uses minutes under an hour', () => {
    expect(formatUpdatedAgo('2026-09-29T11:55:00Z', NOW)).toBe('Updated 5 min ago')
    expect(formatUpdatedAgo('2026-09-29T11:00:01Z', NOW)).toBe('Updated 59 min ago')
  })

  it('uses hours under a day', () => {
    expect(formatUpdatedAgo('2026-09-29T11:00:00Z', NOW)).toBe('Updated 1 h ago')
    expect(formatUpdatedAgo('2026-09-28T13:00:00Z', NOW)).toBe('Updated 23 h ago')
  })

  it('uses days beyond that', () => {
    expect(formatUpdatedAgo('2026-09-28T12:00:00Z', NOW)).toBe('Updated 1 d ago')
    expect(formatUpdatedAgo('2026-09-22T12:00:00Z', NOW)).toBe('Updated 7 d ago')
  })
})
