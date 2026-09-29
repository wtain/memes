import type { StatisticsResponse } from "../types/generated/all"
import { lerpStats } from "./lerpStats"

function makeStats(total: number, tags: number, runs: number): StatisticsResponse {
  return {
    memes: { total, with_tags: total / 2 } as StatisticsResponse["memes"],
    content: { tags } as StatisticsResponse["content"],
    trends: { runs } as StatisticsResponse["trends"],
    computed_at: null,
  }
}

describe("lerpStats", () => {
  const from = makeStats(10, 100, 0)
  const to = { ...makeStats(20, 300, 5), computed_at: "2026-01-01T00:00:00Z" }

  it("t=0 yields from's numbers", () => {
    const r = lerpStats(from, to, 0)
    expect(r.memes.total).toBe(10)
    expect(r.content.tags).toBe(100)
    expect(r.trends.runs).toBe(0)
  })

  it("t=1 yields exactly to's numbers and computed_at", () => {
    expect(lerpStats(from, to, 1)).toEqual(to)
  })

  it("rounds midpoints", () => {
    const r = lerpStats(from, to, 0.5)
    expect(r.memes.total).toBe(15)
    expect(r.content.tags).toBe(200)
    expect(r.trends.runs).toBe(3) // 2.5 rounds up
    expect(r.memes.with_tags).toBe(8) // 5 -> 10, 7.5 rounds up
  })

  it("takes computed_at from to", () => {
    expect(lerpStats(from, to, 0.5).computed_at).toBe("2026-01-01T00:00:00Z")
  })

  it("takes a field present only in to", () => {
    const t = { ...to, memes: { ...to.memes, extra: 42 } }
    expect(lerpStats(from, t, 0.5).memes.extra).toBe(42)
  })

  it("takes non-numeric fields from to", () => {
    const f = { ...from, memes: { ...from.memes, label: "old", n: null } }
    const t = { ...to, memes: { ...to.memes, label: "new", n: 7 } }
    const r = lerpStats(f, t, 0.5)
    expect(r.memes.label).toBe("new")
    expect(r.memes.n).toBe(7)
  })

  it("does not mutate its inputs", () => {
    const f = structuredClone(from)
    const t = structuredClone(to)
    lerpStats(f, t, 0.5)
    expect(f).toEqual(from)
    expect(t).toEqual(to)
  })
})
