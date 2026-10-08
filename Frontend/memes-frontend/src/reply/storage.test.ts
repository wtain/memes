import { loadSaved, save } from "./storage"
import { makeProfile } from "./fixtures"
import { EMPTY_FILTERS } from "./types"

function memoryStorage(initial: Record<string, string> = {}): Storage {
  const data = { ...initial }
  return {
    getItem: (k: string) => data[k] ?? null,
    setItem: (k: string, v: string) => { data[k] = v },
    removeItem: (k: string) => { delete data[k] },
    clear: () => {},
    key: () => null,
    length: 0,
  } as Storage
}

describe("reply storage", () => {
  it("round-trips the saved search", () => {
    const storage = memoryStorage()
    const saved = { text: "hello", submitted: "hello", originalProfile: makeProfile(), editedProfile: null, filters: { ...EMPTY_FILTERS, emotion: ["joy"] } }
    save(storage, saved)
    expect(loadSaved(storage)).toEqual(saved)
  })

  it("returns null for missing, corrupt or wrong-shaped data and for no storage", () => {
    expect(loadSaved(null)).toBeNull()
    expect(loadSaved(memoryStorage())).toBeNull()
    expect(loadSaved(memoryStorage({ "reply.v1": "{not json" }))).toBeNull()
    expect(loadSaved(memoryStorage({ "reply.v1": JSON.stringify({ text: 5 }) }))).toBeNull()
  })

  it("cleans malformed filters", () => {
    const storage = memoryStorage({ "reply.v1": JSON.stringify({ text: "a", filters: { emotion: ["joy", 3], tone: "x" } }) })
    expect(loadSaved(storage)?.filters).toEqual({ emotion: ["joy"], stance: [], topic: [], tone: [] })
  })

  it("treats corrupt profiles as null and keeps the rest of the saved search", () => {
    const stored = { text: "a", submitted: "a", originalProfile: "x", editedProfile: { emotion: "joy", stance: [], topic: [], tone: [], degraded: false, taxonomyVersion: 2 }, filters: {} }
    const loaded = loadSaved(memoryStorage({ "reply.v1": JSON.stringify(stored) }))
    expect(loaded).toMatchObject({ text: "a", submitted: "a", originalProfile: null, editedProfile: null })
    const noArrays = loadSaved(memoryStorage({ "reply.v1": JSON.stringify({ text: "a", originalProfile: { degraded: false } }) }))
    expect(noArrays?.originalProfile).toBeNull()
  })

  it("never throws when storage throws", () => {
    const throwing = { getItem: () => { throw new Error("blocked") }, setItem: () => { throw new Error("quota") } } as unknown as Storage
    expect(loadSaved(throwing)).toBeNull()
    expect(() => save(throwing, { text: "a", submitted: null, originalProfile: null, editedProfile: null, filters: { ...EMPTY_FILTERS } })).not.toThrow()
  })
})
