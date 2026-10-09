import { addKey, emptyDraft, removeKey, sameDraft, toDraft, toSaveRequest } from "./draft"
import { makeCuratedProfile } from "./fixtures"

describe("draft", () => {
  it("builds a draft from a profile", () => {
    const draft = toDraft(makeCuratedProfile({ useWhen: "When late." }))
    expect(draft.situation).toBe("Manager scheduled a late meeting.")
    expect(draft.useWhen).toBe("When late.")
    expect(draft.values).toEqual({ emotion: ["annoyance"], stance: ["sarcasm"], topic: ["work"], tone: [] })
  })

  it("is empty for null and for a profile that does not exist", () => {
    expect(toDraft(null)).toEqual(emptyDraft())
    expect(toDraft(makeCuratedProfile({ exists: false }))).toEqual(emptyDraft())
  })

  it("compares texts trimmed and key lists as sets", () => {
    const a = { situation: "x", useWhen: "", values: { emotion: ["a", "b"], stance: [], topic: [], tone: [] } }
    const b = { situation: " x ", useWhen: "  ", values: { emotion: ["b", "a"], stance: [], topic: [], tone: [] } }
    expect(sameDraft(a, b)).toBe(true)
    expect(sameDraft(a, { ...b, values: { ...b.values, tone: ["t"] } })).toBe(false)
    expect(sameDraft(a, { ...b, situation: "y" })).toBe(false)
  })

  it("builds a save request: trimmed, blank texts null, all four axes", () => {
    const request = toSaveRequest({ situation: "  hi  ", useWhen: "   ", values: { emotion: ["joy"], stance: [], topic: [], tone: [] } })
    expect(request).toEqual({ situation: "hi", useWhen: null, values: { emotion: ["joy"], stance: [], topic: [], tone: [] } })
  })

  it("adds and removes keys without duplicates and without mutating", () => {
    const start = emptyDraft()
    const added = addKey(start, "emotion", "joy")
    expect(added.values.emotion).toEqual(["joy"])
    expect(start.values.emotion).toEqual([])
    expect(addKey(added, "emotion", "joy").values.emotion).toEqual(["joy"])
    expect(removeKey(added, "emotion", "nope").values.emotion).toEqual(["joy"])
    const removed = removeKey(added, "emotion", "joy")
    expect(removed.values.emotion).toEqual([])
    expect(added.values.emotion).toEqual(["joy"])
    expect(removed).not.toBe(added)
  })
})
