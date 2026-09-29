import { act, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { MemesApi } from "../api/MemesApi"
import type { StatisticsResponse } from "../types/generated/all"
import StatisticsPage from "./StatisticsPage"

type Deferred<T> = { promise: Promise<T>; resolve: (v: T) => void; reject: (e: Error) => void }

function deferred<T>(): Deferred<T> {
  let resolve!: (v: T) => void
  let reject!: (e: Error) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

function makeStats(flagged: number, computedAt: string | null = null): StatisticsResponse {
  return {
    memes: {
      total: 10, pending: 1, rejected: 2, with_embeddings: 9, with_ocr: 8, with_tags: 4,
      without_tags: 6, with_descriptions: 5, with_concept_tags: 3, flagged,
      duplicate_clusters: 2, ocr_missing_text_heavy_classification: 1,
      text_heavy_missing_embeddings: 2, embeddings_missing_lemmas: 3,
    },
    content: {
      ocr_texts: 20, tags: 8, tag_keys: 3, tag_values: 6, concepts: 4, concept_image_sets: 2,
      concept_images: 7, descriptions_approved: 1, descriptions_rejected: 1,
      descriptions_feedback_total: 2,
    },
    trends: { runs: 1, trend_sources: 2 },
    computed_at: computedAt,
  }
}

function setup() {
  const snapshot = deferred<StatisticsResponse>()
  const live = deferred<StatisticsResponse>()
  const getStatistics = vi.fn((options?: { live?: boolean }) => (options?.live ? live.promise : snapshot.promise))
  const api = { getStatistics } as unknown as MemesApi
  const view = render(<StatisticsPage memesApi={api} />)
  return { snapshot, live, getStatistics, ...view }
}

async function flush() {
  await act(async () => { await Promise.resolve() })
}

async function advance(ms: number) {
  await act(async () => { vi.advanceTimersByTime(ms) })
}

function cell(label: string): HTMLElement {
  return screen.getByText(label).parentElement as HTMLElement
}

function value(label: string): string | null | undefined {
  return screen.getByText(label).nextElementSibling?.textContent
}

describe("StatisticsPage", () => {
  beforeEach(() => { vi.useFakeTimers() })
  afterEach(() => { vi.useRealTimers() })

  it("shows the snapshot while live is pending and fires both requests once", async () => {
    const { snapshot, getStatistics } = setup()
    expect(screen.getByText("Loading…")).toBeTruthy()

    await act(async () => { snapshot.resolve(makeStats(1)) })

    expect(value("Flagged")).toBe("1")
    expect(screen.getByRole("status").textContent).toContain("Refreshing…")
    expect(getStatistics).toHaveBeenCalledTimes(2)
    expect(getStatistics).toHaveBeenCalledWith()
    expect(getStatistics).toHaveBeenCalledWith({ live: true })
  })

  it("glides to the live values, drops the indicator and reads 'Updated just now'", async () => {
    const { snapshot, live } = setup()
    await act(async () => { snapshot.resolve(makeStats(1, "2020-01-01T00:00:00Z")) })
    expect(screen.getByText(/Updated \d+ d ago/)).toBeTruthy()

    await act(async () => { live.resolve(makeStats(3, new Date(Date.now()).toISOString())) })
    await advance(700)

    expect(value("Flagged")).toBe("3")
    expect(screen.queryByText("Refreshing…")).toBeNull()
    expect(screen.getByText("Updated just now")).toBeTruthy()
  })

  it("highlights changed cells briefly and only those", async () => {
    const { snapshot, live } = setup()
    await act(async () => { snapshot.resolve(makeStats(1)) })
    expect(cell("Flagged").className).not.toContain("bg-amber-100")

    await act(async () => { live.resolve(makeStats(3)) })
    expect(cell("Flagged").className).toContain("bg-amber-100")
    expect(cell("Total memes").className).not.toContain("bg-amber-100")

    await advance(1600)
    expect(cell("Flagged").className).not.toContain("bg-amber-100")
  })

  it("keeps the snapshot silently when the live request fails", async () => {
    const { snapshot, live } = setup()
    await act(async () => { snapshot.resolve(makeStats(1)) })
    await act(async () => { live.reject(new Error("live boom")) })

    expect(value("Flagged")).toBe("1")
    expect(screen.queryByText("Refreshing…")).toBeNull()
    expect(screen.queryByText("live boom")).toBeNull()
    expect(screen.queryByText("Failed to load statistics")).toBeNull()
  })

  it("shows live values without an error when only the snapshot fails", async () => {
    const { snapshot, live } = setup()
    await act(async () => { snapshot.reject(new Error("snapshot boom")) })
    await flush()
    // live still pending: nothing to show yet, but no error either
    expect(screen.queryByText("snapshot boom")).toBeNull()

    await act(async () => { live.resolve(makeStats(3)) })
    await advance(700)

    expect(value("Flagged")).toBe("3")
    expect(screen.queryByText("snapshot boom")).toBeNull()
  })

  it("shows the error when both requests fail", async () => {
    const { snapshot, live } = setup()
    await act(async () => { snapshot.reject(new Error("snapshot boom")) })
    await act(async () => { live.reject(new Error("live boom")) })

    expect(screen.getByText("snapshot boom")).toBeTruthy()
    expect(screen.queryByText("Loading…")).toBeNull()
  })

  it("never lets a late snapshot replace live values that arrived first", async () => {
    const { snapshot, live } = setup()
    await act(async () => { live.resolve(makeStats(3)) })
    expect(value("Flagged")).toBe("3")

    await act(async () => { snapshot.resolve(makeStats(1)) })
    await advance(1000)

    expect(value("Flagged")).toBe("3")
    expect(cell("Flagged").className).not.toContain("bg-amber-100")
  })

  it("unmounting mid-flash and mid-tween produces no warnings", async () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {})
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {})
    const { snapshot, live, unmount } = setup()
    await act(async () => { snapshot.resolve(makeStats(1)) })
    await act(async () => { live.resolve(makeStats(3)) })
    await advance(100)

    unmount()
    await advance(2000)

    expect(errorSpy).not.toHaveBeenCalled()
    expect(warnSpy).not.toHaveBeenCalled()
    errorSpy.mockRestore()
    warnSpy.mockRestore()
  })
})
