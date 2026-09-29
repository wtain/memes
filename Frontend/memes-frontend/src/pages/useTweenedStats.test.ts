import { act, renderHook } from "@testing-library/react"
import type { StatisticsResponse } from "../types/generated/all"
import { TWEEN_DURATION_MS, useTweenedStats } from "./useTweenedStats"

function makeStats(total: number): StatisticsResponse {
  return {
    memes: { total } as StatisticsResponse["memes"],
    content: {} as StatisticsResponse["content"],
    trends: {} as StatisticsResponse["trends"],
    computed_at: null,
  }
}

function mockReducedMotion(reduce: boolean) {
  window.matchMedia = ((query: string) => ({
    matches: reduce && query.includes("prefers-reduced-motion"),
    media: query,
  })) as unknown as typeof window.matchMedia
}

describe("useTweenedStats", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["requestAnimationFrame", "cancelAnimationFrame", "performance", "Date"] })
  })

  afterEach(() => {
    vi.useRealTimers()
    // @ts-expect-error jsdom has no matchMedia; restore that state
    delete window.matchMedia
  })

  it("returns null for a null target", () => {
    const { result } = renderHook(() => useTweenedStats(null))
    expect(result.current).toBeNull()
  })

  it("returns the first non-null target immediately, on initial render", () => {
    const a = makeStats(10)
    const { result } = renderHook(() => useTweenedStats(a))
    expect(result.current).toBe(a)
  })

  it("returns the first non-null target immediately after null", () => {
    const a = makeStats(10)
    const { result, rerender } = renderHook(({ t }) => useTweenedStats(t), {
      initialProps: { t: null as StatisticsResponse | null },
    })
    rerender({ t: a })
    expect(result.current).toBe(a)
  })

  it("keeps showing the old stats right after a change, then glides and settles on the new target", () => {
    const a = makeStats(0)
    const b = makeStats(100)
    const { result, rerender } = renderHook(({ t }) => useTweenedStats(t), { initialProps: { t: a } })

    rerender({ t: b })
    expect(result.current).toEqual(a)

    act(() => { vi.advanceTimersByTime(TWEEN_DURATION_MS / 2) })
    const mid = result.current!.memes.total
    // ease-out: past the linear midpoint, but not finished
    expect(mid).toBeGreaterThan(50)
    expect(mid).toBeLessThan(100)

    act(() => { vi.advanceTimersByTime(TWEEN_DURATION_MS) })
    expect(result.current).toEqual(b)
    expect(result.current).toBe(b)
  })

  it("starts the next glide from the currently shown frame when a target arrives mid-animation", () => {
    const a = makeStats(0)
    const b = makeStats(100)
    const c = makeStats(1000)
    const { result, rerender } = renderHook(({ t }) => useTweenedStats(t), { initialProps: { t: a } })

    rerender({ t: b })
    act(() => { vi.advanceTimersByTime(TWEEN_DURATION_MS / 2) })
    const shown = result.current!.memes.total
    expect(shown).toBeGreaterThan(0)

    rerender({ t: c })
    expect(result.current!.memes.total).toBe(shown)

    // The first frame of the new glide has progress 0, so it shows exactly the
    // interrupted frame: no jump back toward a (0) or forward toward b (100).
    act(() => { vi.advanceTimersByTime(16) })
    expect(result.current!.memes.total).toBe(shown)

    act(() => { vi.advanceTimersByTime(50) })
    expect(result.current!.memes.total).toBeGreaterThan(shown)

    act(() => { vi.advanceTimersByTime(TWEEN_DURATION_MS * 2) })
    expect(result.current).toEqual(c)
  })

  it("applies the new target immediately under prefers-reduced-motion", () => {
    mockReducedMotion(true)
    const a = makeStats(0)
    const b = makeStats(100)
    const { result, rerender } = renderHook(({ t }) => useTweenedStats(t), { initialProps: { t: a } })
    rerender({ t: b })
    expect(result.current).toBe(b)
  })

  it("still animates when the motion preference is not 'reduce'", () => {
    mockReducedMotion(false)
    const a = makeStats(0)
    const b = makeStats(100)
    const { result, rerender } = renderHook(({ t }) => useTweenedStats(t), { initialProps: { t: a } })
    rerender({ t: b })
    expect(result.current).toEqual(a)
  })

  it("applies the new target immediately when durationMs <= 0", () => {
    const a = makeStats(0)
    const b = makeStats(100)
    const { result, rerender } = renderHook(({ t }) => useTweenedStats(t, 0), { initialProps: { t: a } })
    rerender({ t: b })
    expect(result.current).toBe(b)
  })

  it("cancels the pending frame on unmount", () => {
    const cancelSpy = vi.spyOn(window, "cancelAnimationFrame")
    const a = makeStats(0)
    const b = makeStats(100)
    const { rerender, unmount } = renderHook(({ t }) => useTweenedStats(t), { initialProps: { t: a } })
    rerender({ t: b })
    act(() => { vi.advanceTimersByTime(16) })
    cancelSpy.mockClear()
    unmount()
    expect(cancelSpy).toHaveBeenCalled()
    const errorSpy = vi.spyOn(console, "error")
    act(() => { vi.advanceTimersByTime(TWEEN_DURATION_MS * 2) })
    expect(errorSpy).not.toHaveBeenCalled()
    errorSpy.mockRestore()
    cancelSpy.mockRestore()
  })

  it("does not restart when re-rendered with the same target reference", () => {
    const a = makeStats(0)
    const b = makeStats(100)
    const { result, rerender } = renderHook(({ t }) => useTweenedStats(t), { initialProps: { t: a } })
    rerender({ t: b })
    act(() => { vi.advanceTimersByTime(TWEEN_DURATION_MS / 2) })
    const before = result.current!.memes.total

    rerender({ t: b })
    expect(result.current!.memes.total).toBe(before)

    act(() => { vi.advanceTimersByTime(TWEEN_DURATION_MS / 4) })
    const later = result.current!.memes.total
    expect(later).toBeGreaterThanOrEqual(before)
    act(() => { vi.advanceTimersByTime(TWEEN_DURATION_MS) })
    expect(result.current).toBe(b)
  })
})
