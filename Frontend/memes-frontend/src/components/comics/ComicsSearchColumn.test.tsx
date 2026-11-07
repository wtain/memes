import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import ComicsSearchColumn from "./ComicsSearchColumn"
import { makeMockApi } from "../../test/mockApi"

const free = (imageId: string) => ({ imageId, addable: true, reason: null, comicsId: null, comicsName: null })

describe("ComicsSearchColumn addability chunking", () => {
  it("sends at most 100 ids per request and gives every loaded item a state", async () => {
    const memes = (from: number, to: number) => Array.from({ length: to - from }, (_, i) => ({
      id: `m${from + i}`, imageUrl: `/i/${from + i}`, originalFileName: `m${from + i}.jpg`,
    }))
    const searchMemes = vi.fn()
      .mockResolvedValueOnce({ items: memes(0, 60), nextCursor: "c1", hasNext: true })
      .mockResolvedValueOnce({ items: memes(60, 120), nextCursor: null, hasNext: false })
    const comicsAddability = vi.fn((ids: string[]) => Promise.resolve({ items: ids.map(free) }))
    const api = makeMockApi({ searchMemes, comicsAddability })
    const onAdd = vi.fn().mockResolvedValue("conflict")
    const view = render(
      <MemoryRouter>
        <ComicsSearchColumn memesApi={api} comicsId="c1" panelSignature="a" busy={false} onAdd={onAdd} />
      </MemoryRouter>,
    )
    await userEvent.type(screen.getByLabelText("Search memes"), "cat")
    await userEvent.click(screen.getByRole("button", { name: "Search" }))
    await userEvent.click(await screen.findByRole("button", { name: "Show more" }))
    await waitFor(() => expect(
      screen.getAllByRole("button", { name: /^Add m/ }).filter((b) => !(b as HTMLButtonElement).disabled),
    ).toHaveLength(120))

    // a conflict re-checks all 120 loaded items
    comicsAddability.mockClear()
    await userEvent.click(screen.getByRole("button", { name: "Add m5.jpg" }))
    await waitFor(() => expect(comicsAddability.mock.calls.flatMap(([ids]) => ids)).toHaveLength(120))
    for (const [ids] of comicsAddability.mock.calls) expect(ids.length).toBeLessThanOrEqual(100)

    // a panel change does the same
    comicsAddability.mockClear()
    view.rerender(
      <MemoryRouter>
        <ComicsSearchColumn memesApi={api} comicsId="c1" panelSignature="b" busy={false} onAdd={onAdd} />
      </MemoryRouter>,
    )
    await waitFor(() => expect(comicsAddability.mock.calls.flatMap(([ids]) => ids)).toHaveLength(120))
    for (const [ids] of comicsAddability.mock.calls) expect(ids.length).toBeLessThanOrEqual(100)
  })
})

describe("ComicsSearchColumn id lookup", () => {
  const ID = "0f8fad5b-d9cb-469f-a165-70867728950e"
  const meme = (id: string) => ({ id, imageUrl: `/i/${id}`, originalFileName: `${id}.jpg` })

  async function search(api: ReturnType<typeof makeMockApi>, text: string) {
    render(
      <MemoryRouter>
        <ComicsSearchColumn memesApi={api} comicsId="c1" panelSignature="a" busy={false} onAdd={vi.fn()} />
      </MemoryRouter>,
    )
    await userEvent.type(screen.getByLabelText("Search memes"), text)
    await userEvent.click(screen.getByRole("button", { name: "Search" }))
  }

  it("puts the meme found by id first, drops its text duplicate and checks its addability", async () => {
    const searchMemes = vi.fn().mockResolvedValue({ items: [meme("t1"), meme(ID), meme("t2")], nextCursor: null, hasNext: false })
    const comicsAddability = vi.fn((ids: string[]) => Promise.resolve({ items: ids.map(free) }))
    const api = makeMockApi({ searchMemes, comicsAddability, getMeme: vi.fn().mockResolvedValue(meme(ID)) })
    await search(api, `http://x/meme/${ID.toUpperCase()}`)
    await screen.findByText(`${ID}.jpg`)
    const labels = screen.getAllByRole("img").map((img) => img.getAttribute("alt"))
    expect(labels).toEqual([`${ID}.jpg`, "t1.jpg", "t2.jpg"])
    expect(api.getMeme).toHaveBeenCalledWith(ID)
    await waitFor(() => expect(comicsAddability).toHaveBeenCalledWith([ID, "t1", "t2"]))
  })

  it("adds the meme found by id when the text search finds nothing", async () => {
    const api = makeMockApi({
      searchMemes: vi.fn().mockResolvedValue({ items: [], nextCursor: null, hasNext: false }),
      getMeme: vi.fn().mockResolvedValue(meme(ID)),
      comicsAddability: vi.fn((ids: string[]) => Promise.resolve({ items: ids.map(free) })),
    })
    await search(api, ID)
    expect(await screen.findByText(`${ID}.jpg`)).toBeInTheDocument()
    expect(screen.queryByText("No memes found")).not.toBeInTheDocument()
  })

  it("ignores a failed lookup without failing the search", async () => {
    const api = makeMockApi({
      searchMemes: vi.fn().mockResolvedValue({ items: [meme("t1")], nextCursor: null, hasNext: false }),
      getMeme: vi.fn().mockRejectedValue(new Error("404")),
    })
    await search(api, ID)
    expect(await screen.findByText("t1.jpg")).toBeInTheDocument()
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
  })

  it("does not look anything up for a plain text query", async () => {
    const api = makeMockApi({ searchMemes: vi.fn().mockResolvedValue({ items: [meme("t1")], nextCursor: null, hasNext: false }) })
    await search(api, "cat")
    await screen.findByText("t1.jpg")
    expect(api.getMeme).not.toHaveBeenCalled()
  })
})
