import { addValue, hasAnyValue, removeValue, setSituation, toEditedProfile } from "./profile"
import { makeProfile } from "./fixtures"

describe("profile edits", () => {
  it("adds a value as a human value with full confidence", () => {
    const next = addValue(makeProfile(), "emotion", "joy", 3)
    expect(next.emotion.map((v) => v.key)).toEqual(["annoyance", "joy"])
    expect(next.emotion[1]).toEqual({ key: "joy", confidence: 1, source: "human" })
  })

  it("returns the same object for a duplicate or when the axis is full", () => {
    const profile = makeProfile()
    expect(addValue(profile, "emotion", "annoyance", 3)).toBe(profile)
    expect(addValue(profile, "emotion", "joy", 1)).toBe(profile)
  })

  it("removes a value, and returns the same object when it is absent", () => {
    const profile = makeProfile()
    expect(removeValue(profile, "emotion", "annoyance").emotion).toEqual([])
    expect(removeValue(profile, "emotion", "nothing")).toBe(profile)
  })

  it("sets the situation, caps it at 160 characters and treats only the empty string as none", () => {
    const profile = makeProfile()
    expect(setSituation(profile, "x".repeat(300)).situation).toHaveLength(160)
    expect(setSituation(profile, "").situation).toBeNull()
    expect(setSituation(profile, " ").situation).toBe(" ")
    expect(setSituation(profile, " leading").situation).toBe(" leading")
    expect(setSituation(profile, profile.situation as string)).toBe(profile)
  })

  it("marks an edited profile as not degraded", () => {
    expect(toEditedProfile(makeProfile({ degraded: true })).degraded).toBe(false)
  })

  it("knows whether a profile holds anything", () => {
    expect(hasAnyValue(makeProfile())).toBe(true)
    expect(hasAnyValue(makeProfile({ situation: "  ", emotion: [], stance: [], topic: [], tone: [] }))).toBe(false)
    expect(hasAnyValue(makeProfile({ situation: null, emotion: [], stance: [], topic: [], tone: [] }))).toBe(false)
  })
})
