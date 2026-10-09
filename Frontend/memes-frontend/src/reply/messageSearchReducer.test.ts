import { activeProfile, initialState, reducer, statusFor } from "./messageSearchReducer"
import type { State } from "./messageSearchReducer"
import { makeProfile, makeResponse, makeResult } from "./fixtures"
import { EMPTY_FILTERS } from "./types"

const base = initialState(null)

describe("messageSearchReducer", () => {
  it("starts idle, or refining when a saved search is restored", () => {
    expect(base.status).toBe("idle")
    const restored = initialState({ text: "a", submitted: "a", originalProfile: makeProfile(), editedProfile: null, filters: { ...EMPTY_FILTERS } })
    expect(restored.status).toBe("refining")
    expect(restored.text).toBe("a")
  })

  it("a new find clears the previous reading, filters and results", () => {
    const dirty = { ...base, editedProfile: makeProfile(), originalProfile: makeProfile(), items: [makeResult("a")], filters: { ...EMPTY_FILTERS, tone: ["x"] } }
    const next = reducer(dirty, { type: "findStarted", submitted: "new" })
    expect(next).toMatchObject({ submitted: "new", status: "reading", items: [], editedProfile: null, originalProfile: null })
    expect(next.filters).toEqual(EMPTY_FILTERS)
  })

  it("a find result stores the reading, results, cursor, the replayable request and the status", () => {
    const response = makeResponse({ items: [makeResult("a")], hasNext: true, nextCursor: "c1" })
    const request = { text: "hello", limit: 20 }
    const next = reducer({ ...base, status: "reading" }, { type: "succeeded", mode: "find", response, request })
    expect(next.status).toBe("ready")
    expect(next.items.map((r) => r.meme.id)).toEqual(["a"])
    expect(next.originalProfile).toEqual(response.profile)
    expect(next.nextCursor).toBe("c1")
    expect(next.lastRequest).toEqual(request)
  })

  it("a refine result keeps the original reading", () => {
    const original = makeProfile({ situation: "original" })
    const response = makeResponse({ items: [makeResult("b")], profile: makeProfile({ situation: "edited" }) })
    const next = reducer({ ...base, originalProfile: original }, { type: "succeeded", mode: "refine", response, request: { text: "t" } })
    expect(next.originalProfile).toBe(original)
  })

  it("maps availability and degradation to the status", () => {
    expect(statusFor(makeResponse())).toBe("ready")
    expect(statusFor(makeResponse({ degraded: true, fallback: "unavailable" }))).toBe("degraded")
    const unavailable = makeResponse()
    unavailable.meta.available = false
    expect(statusFor(unavailable)).toBe("unavailable")
  })

  it("show more appends, and strips the cursor from the stored request", () => {
    const start = { ...base, items: [makeResult("a")], nextCursor: "c1", hasNext: true }
    const response = makeResponse({ items: [makeResult("b")], hasNext: false })
    const next = reducer(start, { type: "succeeded", mode: "more", response, request: { text: "t", cursor: "c1" } })
    expect(next.items.map((r) => r.meme.id)).toEqual(["a", "b"])
    expect(next.lastRequest).toEqual({ text: "t" })
    expect(next.nextCursor).toBeNull()
  })

  it("show more replaces the list when the server restarted it (stale cursor)", () => {
    const start = { ...base, items: [makeResult("a"), makeResult("b")], nextCursor: "c1", hasNext: true }
    const response = makeResponse({ items: [makeResult("a"), makeResult("b"), makeResult("c")] })
    const next = reducer(start, { type: "succeeded", mode: "more", response, request: { text: "t", cursor: "c1" } })
    expect(next.items.map((r) => r.meme.id)).toEqual(["a", "b", "c"])
  })

  it("a failure keeps the results and records the kind", () => {
    const start = { ...base, items: [makeResult("a")], loadingMore: true }
    const next = reducer(start, { type: "failed", error: "validation" })
    expect(next).toMatchObject({ status: "error", error: "validation", loadingMore: false })
    expect(next.items).toHaveLength(1)
  })

  it("edits, reset and filters update only their own fields; the active profile prefers the edit", () => {
    const original = makeProfile({ situation: "o" })
    const edited = makeProfile({ situation: "e" })
    let state: State = { ...base, originalProfile: original }
    expect(activeProfile(state)).toBe(original)
    state = reducer(state, { type: "edited", profile: edited })
    expect(activeProfile(state)).toBe(edited)
    state = reducer(state, { type: "resetEdit" })
    expect(activeProfile(state)).toBe(original)
    state = reducer(state, { type: "filters", filters: { ...EMPTY_FILTERS, tone: ["wholesome"] } })
    expect(state.filters.tone).toEqual(["wholesome"])
  })

  describe("expired server cache and availability", () => {
    const more = (state: State, response: ReturnType<typeof makeResponse>) =>
      reducer(state, { type: "succeeded", mode: "more", response, request: { text: "t", cursor: "c1" } })
    const listed = (status: "ready" | "degraded"): State => ({
      ...base, status, items: [makeResult("a"), makeResult("b")], nextCursor: "c1", hasNext: true, fallback: status === "degraded" ? "unavailable" : "none",
    })

    it("show more drops items that are already in the list", () => {
      const next = more(listed("ready"), makeResponse({ items: [makeResult("b"), makeResult("c")] }))
      expect(next.items.map((r) => r.meme.id)).toEqual(["a", "b", "c"])
    })

    it("a normal page does not change the status, the fallback or the meta", () => {
      const start = listed("degraded")
      const response = makeResponse({ items: [makeResult("c")], degraded: true, fallback: "timeout" })
      response.meta.parseMs = 1
      const next = more(start, response)
      expect(next.status).toBe("degraded")
      expect(next.fallback).toBe("unavailable")
      expect(next.meta).toBe(start.meta)
      expect(next.nextCursor).toBeNull()
    })

    it("a degraded page after a healthy list replaces the list and takes the new status", () => {
      const next = more(listed("ready"), makeResponse({ items: [makeResult("x"), makeResult("y")], degraded: true, fallback: "timeout" }))
      expect(next.items.map((r) => r.meme.id)).toEqual(["x", "y"])
      expect(next.status).toBe("degraded")
      expect(next.fallback).toBe("timeout")
    })

    it("a healthy page after a degraded list replaces the list and takes the new status", () => {
      const next = more(listed("degraded"), makeResponse({ items: [makeResult("x")] }))
      expect(next.items.map((r) => r.meme.id)).toEqual(["x"])
      expect(next.status).toBe("ready")
      expect(next.fallback).toBe("none")
    })

    it("tracks whether a list exists and counts responses", () => {
      expect(base.hasList).toBe(false)
      const done = reducer({ ...base, status: "reading" }, { type: "succeeded", mode: "find", response: makeResponse(), request: { text: "t" } })
      expect(done).toMatchObject({ hasList: true, searchCount: 1 })
      expect(reducer(done, { type: "failed", error: "failed" }).hasList).toBe(true)
      expect(reducer(done, { type: "findStarted", submitted: "n" }).hasList).toBe(false)
    })

    it("unavailable sets the status without an error", () => {
      expect(reducer({ ...base, status: "refining", loadingMore: true }, { type: "unavailable" })).toMatchObject({ status: "unavailable", error: null, loadingMore: false })
    })
  })
})

describe("listReading", () => {
  const ok = (state: State, mode: "find" | "refine" | "more", reading?: "parsed" | "edited") =>
    reducer(state, { type: "succeeded", mode, response: makeResponse({ items: [makeResult("x")], hasNext: true, nextCursor: "c" }), request: { text: "t" }, reading })

  it("starts parsed, is set from the request on success, and kept on Show more", () => {
    expect(base.listReading).toBe("parsed")
    const edited = ok({ ...base, status: "refining" }, "refine", "edited")
    expect(edited.listReading).toBe("edited")
    expect(ok({ ...edited, nextCursor: "c", lastRequest: { text: "t" } }, "more").listReading).toBe("edited")
  })

  it("is unchanged by a failure and reset by a new find", () => {
    const edited = ok({ ...base, status: "refining" }, "refine", "edited")
    expect(reducer(edited, { type: "failed", error: "failed" }).listReading).toBe("edited")
    expect(reducer(edited, { type: "findStarted", submitted: "n" }).listReading).toBe("parsed")
  })
})
