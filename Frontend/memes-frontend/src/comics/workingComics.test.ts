import {
  clearWorkingComics, getWorkingComics, setWorkingComics, subscribeWorkingComics, WORKING_COMICS_KEY,
} from "./workingComics"

afterEach(() => {
  vi.restoreAllMocks()
  clearWorkingComics()
})

describe("workingComics", () => {
  it("is empty at first", () => {
    expect(getWorkingComics()).toBeNull()
  })

  it("sets, reads and clears, notifying subscribers", () => {
    const listener = vi.fn()
    const unsubscribe = subscribeWorkingComics(listener)
    setWorkingComics({ id: "c1", name: "Cats" })
    expect(getWorkingComics()).toEqual({ id: "c1", name: "Cats" })
    expect(getWorkingComics()).toBe(getWorkingComics())
    expect(localStorage.getItem(WORKING_COMICS_KEY)).toBe(JSON.stringify({ id: "c1", name: "Cats" }))
    clearWorkingComics()
    expect(getWorkingComics()).toBeNull()
    expect(listener).toHaveBeenCalledTimes(2)
    unsubscribe()
    setWorkingComics({ id: "c2", name: "Dogs" })
    expect(listener).toHaveBeenCalledTimes(2)
  })

  it("ignores a malformed stored value", () => {
    localStorage.setItem(WORKING_COMICS_KEY, "{not json")
    expect(getWorkingComics()).toBeNull()
    localStorage.setItem(WORKING_COMICS_KEY, JSON.stringify({ id: 1 }))
    expect(getWorkingComics()).toBeNull()
  })

  it("keeps the value in memory when storage fails", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("denied") })
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("denied") })
    setWorkingComics({ id: "c1", name: "Cats" })
    expect(getWorkingComics()).toEqual({ id: "c1", name: "Cats" })
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => { throw new Error("denied") })
    clearWorkingComics()
    expect(getWorkingComics()).toBeNull()
  })
})
