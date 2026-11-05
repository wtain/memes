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
