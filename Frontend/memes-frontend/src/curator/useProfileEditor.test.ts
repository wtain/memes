import { act, renderHook, waitFor } from "@testing-library/react"
import { StrictMode } from "react"
import { useProfileEditor } from "./useProfileEditor"
import { makeCuratedProfile } from "./fixtures"
import { toDraft, toSaveRequest } from "./draft"
import { makeMockApi } from "../test/mockApi"

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

async function ready(api = makeMockApi(), id = "img-1") {
  const hook = renderHook(({ imageId }) => useProfileEditor(api, imageId), { initialProps: { imageId: id } })
  await waitFor(() => expect(hook.result.current.status).toBe("ready"))
  return { api, ...hook }
}

describe("useProfileEditor", () => {
  it("loads the profile", async () => {
    const api = makeMockApi()
    const { result } = renderHook(() => useProfileEditor(api, "img-1"))
    expect(result.current.status).toBe("loading")
    await waitFor(() => expect(result.current.status).toBe("ready"))
    expect(api.getCuratedProfile).toHaveBeenCalledWith("img-1")
    expect(result.current.draft).toEqual(toDraft(makeCuratedProfile()))
    expect(result.current.dirty).toBe(false)
  })

  it("loads under StrictMode", async () => {
    const api = makeMockApi()
    const { result } = renderHook(() => useProfileEditor(api, "img-1"), { wrapper: StrictMode })
    await waitFor(() => expect(result.current.status).toBe("ready"))
    expect(result.current.profile?.imageId).toBe("img-1")
  })

  it("is dirty after an edit and clean after editing back", async () => {
    const { result } = await ready()
    act(() => result.current.setSituation("Other"))
    expect(result.current.dirty).toBe(true)
    act(() => result.current.setSituation("Manager scheduled a late meeting."))
    expect(result.current.dirty).toBe(false)
  })

  it("saves the draft, replaces the profile, clears edits and reports saved", async () => {
    const saved = makeCuratedProfile({ situation: "New", humanVerified: true, vector: "pending" })
    const api = makeMockApi({ saveCuratedProfile: vi.fn().mockResolvedValue(saved) })
    const { result } = await ready(api)
    act(() => result.current.setSituation("New"))
    const expected = toSaveRequest(result.current.draft)
    await act(async () => { await result.current.save() })
    expect(api.saveCuratedProfile).toHaveBeenCalledWith("img-1", expected)
    expect(result.current.profile).toEqual(saved)
    expect(result.current.dirty).toBe(false)
    expect(result.current.event).toEqual({ kind: "saved", vector: "pending" })
    expect(result.current.busy).toBeNull()
  })

  it("keeps the edits when a save fails", async () => {
    const api = makeMockApi({ saveCuratedProfile: vi.fn().mockRejectedValue(new Error("down")) })
    const { result } = await ready(api)
    act(() => result.current.setSituation("New"))
    await act(async () => { await result.current.save() })
    expect(result.current.event).toEqual({ kind: "failed", action: "save" })
    expect(result.current.dirty).toBe(true)
    expect(result.current.draft.situation).toBe("New")
    expect(result.current.busy).toBeNull()
  })

  it("confirms", async () => {
    const { result, api } = await ready()
    await act(async () => { await result.current.confirm() })
    expect(api.confirmCuratedProfile).toHaveBeenCalledWith("img-1")
    expect(result.current.event).toEqual({ kind: "confirmed" })
    expect(result.current.profile?.humanVerified).toBe(true)
  })

  it("resets to an empty profile of the same vocabulary version and clears edits", async () => {
    const { result, api } = await ready()
    act(() => result.current.setSituation("Edited"))
    await act(async () => { await result.current.reset() })
    expect(api.resetCuratedProfile).toHaveBeenCalledWith("img-1")
    expect(api.getCuratedProfile).toHaveBeenCalledTimes(1)
    expect(result.current.profile?.exists).toBe(false)
    expect(result.current.profile?.currentTaxonomyVersion).toBe(2)
    expect(result.current.dirty).toBe(false)
    expect(result.current.event).toEqual({ kind: "reset" })
  })

  it("leaves the profile when a reset fails", async () => {
    const api = makeMockApi({ resetCuratedProfile: vi.fn().mockRejectedValue(new Error("down")) })
    const { result } = await ready(api)
    await act(async () => { await result.current.reset() })
    expect(result.current.event).toEqual({ kind: "failed", action: "reset" })
    expect(result.current.profile?.exists).toBe(true)
  })

  it("ignores a second action while busy", async () => {
    const pending = deferred<ReturnType<typeof makeCuratedProfile>>()
    const api = makeMockApi({ confirmCuratedProfile: vi.fn().mockReturnValue(pending.promise) })
    const { result } = await ready(api)
    act(() => { void result.current.confirm(); void result.current.confirm(); void result.current.reset() })
    expect(result.current.busy).toBe("confirm")
    expect(api.confirmCuratedProfile).toHaveBeenCalledTimes(1)
    expect(api.resetCuratedProfile).not.toHaveBeenCalled()
    await act(async () => { pending.resolve(makeCuratedProfile({ humanVerified: true })) })
    expect(result.current.busy).toBeNull()
  })

  it("discards the edits", async () => {
    const { result } = await ready()
    act(() => result.current.add("tone", "wholesome"))
    expect(result.current.dirty).toBe(true)
    act(() => result.current.discard())
    expect(result.current.dirty).toBe(false)
  })

  it("reports a failed load", async () => {
    const api = makeMockApi({ getCuratedProfile: vi.fn().mockRejectedValue(new Error("down")) })
    const { result } = renderHook(() => useProfileEditor(api, "img-1"))
    await waitFor(() => expect(result.current.status).toBe("error"))
  })

  it("reloads and drops edits when the image changes", async () => {
    const api = makeMockApi({
      getCuratedProfile: vi.fn().mockImplementation((id: string) => Promise.resolve(makeCuratedProfile({ imageId: id, situation: `S ${id}` }))),
    })
    const { result, rerender } = await ready(api)
    act(() => result.current.setSituation("Edited"))
    rerender({ imageId: "img-2" })
    expect(result.current.status).toBe("loading")
    await waitFor(() => expect(result.current.status).toBe("ready"))
    expect(result.current.draft.situation).toBe("S img-2")
    expect(result.current.dirty).toBe(false)
  })
})
