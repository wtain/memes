import { parseSearchParams, buildSearchParams } from './searchParams'

describe('parseSearchParams', () => {
  it('parses query string', () => {
    const params = new URLSearchParams('q=cats')
    expect(parseSearchParams(params).query).toBe('cats')
  })

  it('returns empty string when q is absent', () => {
    const params = new URLSearchParams()
    expect(parseSearchParams(params).query).toBe('')
  })

  it('parses cursor', () => {
    const params = new URLSearchParams('cursor=abc123')
    expect(parseSearchParams(params).cursor).toBe('abc123')
  })

  it('returns undefined cursor when absent', () => {
    const params = new URLSearchParams()
    expect(parseSearchParams(params).cursor).toBeUndefined()
  })

  it('parses repeated filter keys into arrays', () => {
    const params = new URLSearchParams('category=cats&category=dogs')
    expect(parseSearchParams(params).filters).toEqual({ category: ['cats', 'dogs'] })
  })

  it('ignores q and cursor from filters', () => {
    const params = new URLSearchParams('q=test&cursor=x&tag=funny')
    const { filters } = parseSearchParams(params)
    expect(filters).not.toHaveProperty('q')
    expect(filters).not.toHaveProperty('cursor')
    expect(filters).toEqual({ tag: ['funny'] })
  })

  it('returns empty filters when no filter params', () => {
    const params = new URLSearchParams('q=test')
    expect(parseSearchParams(params).filters).toEqual({})
  })
})

describe('buildSearchParams', () => {
  it('sets q param for non-empty query', () => {
    const params = buildSearchParams('cats', {})
    expect(params.get('q')).toBe('cats')
  })

  it('omits q param for empty query', () => {
    const params = buildSearchParams('', {})
    expect(params.has('q')).toBe(false)
  })

  it('sets cursor when provided', () => {
    const params = buildSearchParams('', {}, 'page2')
    expect(params.get('cursor')).toBe('page2')
  })

  it('omits cursor when undefined', () => {
    const params = buildSearchParams('', {})
    expect(params.has('cursor')).toBe(false)
  })

  it('appends multiple values for the same filter key', () => {
    const params = buildSearchParams('', { category: ['cats', 'dogs'] })
    expect(params.getAll('category')).toEqual(['cats', 'dogs'])
  })

  it('round-trips through parse then build', () => {
    const original = new URLSearchParams('q=funny&category=cats&category=dogs&cursor=abc')
    const parsed = parseSearchParams(original)
    const rebuilt = buildSearchParams(parsed.query, parsed.filters, parsed.cursor)
    expect(rebuilt.get('q')).toBe('funny')
    expect(rebuilt.get('cursor')).toBe('abc')
    expect(rebuilt.getAll('category').sort()).toEqual(['cats', 'dogs'])
  })
})
