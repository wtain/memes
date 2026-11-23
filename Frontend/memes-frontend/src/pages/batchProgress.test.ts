import { describeProgress, formatEta, isStalled, progressFraction, STALE_AFTER_MS } from './batchProgress'

const NOW = Date.parse('2026-11-23T12:00:00Z')
const iso = (msAgo: number) => new Date(NOW - msAgo).toISOString()

function prog(over: Partial<Parameters<typeof describeProgress>[0]> = {}) {
  return {
    label: 'images', unit: 'images', done: 5203, total: 12336, rate_per_min: 19.7,
    step: null, updated_at: iso(20_000), ...over,
  }
}

describe('progressFraction', () => {
  it('divides done by total', () => {
    expect(progressFraction({ done: 5203, total: 12336 })).toBeCloseTo(0.4218, 3)
  })
  it('is null without a usable total or done', () => {
    expect(progressFraction({ done: 0, total: 0 })).toBeNull()
    expect(progressFraction({ done: 5, total: null })).toBeNull()
    expect(progressFraction({ done: null, total: 5 })).toBeNull()
  })
  it('clamps to 1', () => {
    expect(progressFraction({ done: 12, total: 10 })).toBe(1)
  })
})

describe('formatEta', () => {
  it('reports hours', () => {
    expect(formatEta({ done: 5203, total: 12336, rate_per_min: 19.7 })).toBe('about 6 h left')
  })
  it('reports minutes and under a minute', () => {
    expect(formatEta({ done: 0, total: 120, rate_per_min: 10 })).toBe('about 12 min left')
    expect(formatEta({ done: 95, total: 100, rate_per_min: 60 })).toBe('under 1 min left')
  })
  it('is null without a rate, a total, or when finished', () => {
    expect(formatEta({ done: 1, total: 10, rate_per_min: null })).toBeNull()
    expect(formatEta({ done: 1, total: 10, rate_per_min: 0 })).toBeNull()
    expect(formatEta({ done: 1, total: null, rate_per_min: 5 })).toBeNull()
    expect(formatEta({ done: 10, total: 10, rate_per_min: 5 })).toBeNull()
    expect(formatEta({ done: 12, total: 10, rate_per_min: 5 })).toBeNull()
  })
})

describe('isStalled', () => {
  it('is true only for a running run with an old update', () => {
    expect(isStalled(iso(10_000), NOW, 'running')).toBe(false)
    expect(isStalled(iso(6 * 60_000), NOW, 'running')).toBe(true)
    expect(isStalled(iso(STALE_AFTER_MS), NOW, 'running')).toBe(false)
    expect(isStalled(iso(6 * 60_000), NOW, 'completed')).toBe(false)
  })
})

describe('describeProgress', () => {
  it('formats counts with separators, eta, step and age', () => {
    const d = describeProgress(prog({ step: { index: 2, count: 8, label: 'extract_text_from_memes' } }), NOW)
    expect(d.counts).toBe('5,203 / 12,336 images')
    expect(d.bar).toBeCloseTo(0.4218, 3)
    expect(d.eta).toBe('about 6 h left')
    expect(d.step).toBe('step 2 of 8: extract_text_from_memes')
    expect(d.updated).toBe('updated 20 s ago')
    expect(d.stalled).toBe(false)
  })
  it('falls back to "items" when unit is null', () => {
    expect(describeProgress(prog({ unit: null }), NOW).counts).toBe('5,203 / 12,336 items')
  })
  it('never prints NaN, Infinity or undefined', () => {
    const cases = [
      prog({ total: 0, done: 0 }),
      prog({ total: null }),
      prog({ done: 20, total: 10 }),
      prog({ done: null, total: null, rate_per_min: null, unit: null, label: null,
        step: { index: 1, count: 3, label: 'a' } }),
      prog({ updated_at: 'garbage' }),
    ]
    for (const c of cases) {
      const d = describeProgress(c, NOW)
      const text = JSON.stringify(d)
      expect(text).not.toMatch(/NaN|Infinity|undefined|null"?,"eta/)
      expect(d.counts).not.toMatch(/NaN|Infinity|undefined/)
    }
  })
  it('uses an empty counts string for a step-only value', () => {
    const d = describeProgress(prog({ done: null, total: null, step: { index: 1, count: 3, label: 'a' } }), NOW)
    expect(d.counts).toBe('')
    expect(d.bar).toBeNull()
  })
  it('shows done alone when total is unknown', () => {
    expect(describeProgress(prog({ total: null }), NOW).counts).toBe('5,203 images')
  })
  it('says "no update for N min" when stalled', () => {
    const d = describeProgress(prog({ updated_at: iso(7 * 60_000 + 30_000) }), NOW)
    expect(d.stalled).toBe(true)
    expect(d.updated).toBe('no update for 7 min')
  })
  it('never says fewer than 5 min once stalled', () => {
    const d = describeProgress(prog({ updated_at: iso(300_001) }), NOW)
    expect(d.stalled).toBe(true)
    expect(d.updated).toBe('no update for 5 min')
  })
  it('keeps "updated N min ago" before the stall threshold', () => {
    const d = describeProgress(prog({ updated_at: iso(4 * 60_000) }), NOW)
    expect(d.stalled).toBe(false)
    expect(d.updated).toBe('updated 4 min ago')
  })
})
