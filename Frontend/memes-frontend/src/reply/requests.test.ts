import { buildRequest, profileToSend } from "./requests"
import { initialState } from "./messageSearchReducer"
import { makeProfile } from "./fixtures"
import { EMPTY_FILTERS } from "./types"

const state = (patch = {}) => ({ ...initialState(null), text: "  hello  ", submitted: "hello", ...patch })

describe("buildRequest", () => {
  it("find sends only the trimmed text and the page size", () => {
    expect(buildRequest(state(), "find")).toEqual({ text: "hello", limit: 20 })
  })

  it("refine sends the usable profile and the active filters", () => {
    const original = makeProfile()
    const request = buildRequest(state({ originalProfile: original, filters: { ...EMPTY_FILTERS, emotion: ["joy"] } }), "refine")
    expect(request).toEqual({ text: "hello", limit: 20, profile: original, filters: { emotion: ["joy"] } })
  })

  it("an edited profile wins over the original; overrides win over state", () => {
    const edited = makeProfile({ situation: "edited" })
    const s = state({ originalProfile: makeProfile(), editedProfile: edited })
    expect(buildRequest(s, "refine").profile).toBe(edited)
    expect(buildRequest(s, "refine", { editedProfile: null }).profile).toEqual(s.originalProfile)
  })

  it("never sends a degraded original as a profile", () => {
    const degraded = makeProfile({ degraded: true, emotion: [], stance: [], topic: [], tone: [], situation: null })
    const s = state({ originalProfile: degraded })
    expect(profileToSend(s)).toBeNull()
    expect(buildRequest(s, "refine")).toEqual({ text: "hello", limit: 20 })
  })

  it("show more replays the request of the current list with the cursor", () => {
    const last = { text: "hello", limit: 20 }
    const s = state({ lastRequest: last, nextCursor: "c9", originalProfile: makeProfile() })
    expect(buildRequest(s, "more")).toEqual({ text: "hello", limit: 20, cursor: "c9" })
  })
})
