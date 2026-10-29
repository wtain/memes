import { StrictMode } from "react"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import TrendingMemesPage from "./TrendingMemesPage"
import AppLayout from "../app/AppLayout"
import { makeMockApi } from "../test/mockApi"
import type { TrendingMemesResponse } from "../types/generated/all"

function item(id: string, popularity: number) {
  return { meme: { id, imageUrl: `/i/${id}`, text: [], tags: [], flagged: false }, popularity }
}
function page(items: ReturnType<typeof item>[], nextCursor?: string): TrendingMemesResponse {
  return { items, nextCursor, hasNext: nextCursor !== undefined }
}
function setup(overrides: Parameters<typeof makeMockApi>[0], strict = false) {
  const api = makeMockApi(overrides)
  const ui = <MemoryRouter><TrendingMemesPage memesApi={api} /></MemoryRouter>
  render(strict ? <StrictMode>{ui}</StrictMode> : ui)
  return api
}

describe("TrendingMemesPage", () => {
  it("shows a card per item with its popularity badge", async () => {
    setup({ getTrendingMemes: vi.fn().mockResolvedValue(page([item("a", 12), item("b", 7)])) })
    expect(await screen.findByRole("img", { name: "a" })).toBeInTheDocument()
    expect(screen.getByRole("img", { name: "b" })).toBeInTheDocument()
    expect(screen.getByText("Popularity 12")).toBeInTheDocument()
    expect(screen.getByText("Popularity 7")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Show more" })).not.toBeInTheDocument()
  })

  it("Show more passes the cursor and appends without duplicates", async () => {
    const user = userEvent.setup()
    const getTrendingMemes = vi.fn()
      .mockResolvedValueOnce(page([item("a", 5)], "c1"))
      .mockResolvedValueOnce(page([item("a", 5), item("b", 3)]))
    setup({ getTrendingMemes })
    await user.click(await screen.findByRole("button", { name: "Show more" }))
    await screen.findByRole("img", { name: "b" })
    expect(getTrendingMemes.mock.calls[1][1]).toBe("c1")
    expect(screen.getAllByRole("img", { name: "a" })).toHaveLength(1)
    expect(screen.queryByRole("button", { name: "Show more" })).not.toBeInTheDocument()
  })

  it("shows an empty state for an empty first page", async () => {
    setup({ getTrendingMemes: vi.fn().mockResolvedValue(page([])) })
    expect(await screen.findByText("No trending memes yet.")).toBeInTheDocument()
  })

  it("shows an error and no cards when the first request fails", async () => {
    setup({ getTrendingMemes: vi.fn().mockRejectedValue(new Error("boom")) })
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load trending memes")
    expect(screen.queryAllByRole("img")).toHaveLength(0)
  })

  it("opens the details modal on card click", async () => {
    const user = userEvent.setup()
    setup({ getTrendingMemes: vi.fn().mockResolvedValue(page([item("a", 1)])) })
    await user.click(await screen.findByRole("img", { name: "a" }))
    expect(await screen.findByText("Meme Details")).toBeInTheDocument()
  })

  it("makes one first request under StrictMode", async () => {
    const getTrendingMemes = vi.fn().mockResolvedValue(page([item("a", 1)]))
    setup({ getTrendingMemes }, true)
    await screen.findByRole("img", { name: "a" })
    await waitFor(() => expect(getTrendingMemes).toHaveBeenCalledTimes(1))
    expect(screen.getAllByRole("img", { name: "a" })).toHaveLength(1)
  })
})

describe("nav", () => {
  it("has a Trending memes link distinct from Trends", () => {
    render(<MemoryRouter><AppLayout /></MemoryRouter>)
    expect(screen.getByRole("link", { name: "Trending memes" })).toHaveAttribute("href", "/trending-memes")
    expect(screen.getByRole("link", { name: "Trends" })).toHaveAttribute("href", "/trends")
  })
})
