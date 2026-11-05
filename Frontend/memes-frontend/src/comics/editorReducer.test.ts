import { initialSearchState, movedOrder, panelSignature, searchReducer } from "./editorReducer"
import type { Meme } from "../types/generated/all"

const meme = (id: string): Meme => ({ id, imageUrl: `/api/images/${id}` })

describe("searchReducer", () => {
  it("replaces results on loaded and appends without duplicates", () => {
    let s = searchReducer(initialSearchState, { type: "loaded", key: "q", items: [meme("a"), meme("b")], nextCursor: "c1", hasNext: true })
    s = searchReducer(s, { type: "appended", key: "q", items: [meme("b"), meme("c")], nextCursor: null, hasNext: false })
    expect(s.items.map((m) => m.id)).toEqual(["a", "b", "c"])
    expect(s.hasNext).toBe(false)
  })

  it("ignores an append for another query", () => {
    const s = searchReducer(initialSearchState, { type: "loaded", key: "q", items: [meme("a")], nextCursor: null, hasNext: false })
    expect(searchReducer(s, { type: "appended", key: "old", items: [meme("z")], nextCursor: null, hasNext: false })).toBe(s)
  })

  it("merges addability by image id", () => {
    const item = (imageId: string, addable: boolean) => ({ imageId, addable, reason: null, comicsId: null, comicsName: null })
    let s = searchReducer(initialSearchState, { type: "addability", items: [item("a", true), item("b", true)] })
    s = searchReducer(s, { type: "addability", items: [item("a", false)] })
    expect(s.addability.a.addable).toBe(false)
    expect(s.addability.b.addable).toBe(true)
  })
})

describe("panelSignature / movedOrder", () => {
  it("signature is null before load and joins ids", () => {
    expect(panelSignature(null)).toBeNull()
    expect(panelSignature([{ id: "a" }, { id: "b" }])).toBe("a,b")
  })
  it("moves and bounds", () => {
    expect(movedOrder(["a", "b", "c"], 1, -1)).toEqual(["b", "a", "c"])
    expect(movedOrder(["a", "b", "c"], 1, 1)).toEqual(["a", "c", "b"])
    expect(movedOrder(["a", "b"], 0, -1)).toBeNull()
    expect(movedOrder(["a", "b"], 1, 1)).toBeNull()
  })
})
