import { StrictMode } from "react"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import TrendingMemesPage from "./TrendingMemesPage"
import AppLayout from "../app/AppLayout"
import { makeMockApi } from "../test/mockApi"
import type { TrendingMemesResponse } from "../types/generated/all"
import { getCardImg, findCardImg, queryCardImg, queryAllCardImgs } from '../test/cardQueries'

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
    expect(await findCardImg("a")).toBeInTheDocument()
    expect(getCardImg("b")).toBeInTheDocument()
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
    await findCardImg("b")
    expect(getTrendingMemes.mock.calls[1][1]).toBe("c1")
    expect(queryAllCardImgs("a")).toHaveLength(1)
    expect(screen.queryByRole("button", { name: "Show more" })).not.toBeInTheDocument()
  })

  it("shows card skeletons while the first page loads", () => {
    setup({ getTrendingMemes: vi.fn().mockReturnValue(new Promise(() => {})) })
    expect(screen.getAllByTestId("meme-card-skeleton")).toHaveLength(6)
    expect(screen.getByRole("status")).toHaveTextContent("Loading...")
  })

  it("empty all-time list explains trending and links to Explore", async () => {
    setup({ getTrendingMemes: vi.fn().mockResolvedValue(page([])) })
    expect(await screen.findByText("Nothing trending yet")).toBeInTheDocument()
    expect(screen.getByText(/the more copies of a meme show up/)).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "Browse memes" })).toHaveAttribute("href", "/explore")
  })

  it("empty 30-day list has its own title", async () => {
    const getTrendingMemes = vi.fn().mockResolvedValue(page([]))
    render(<MemoryRouter initialEntries={["/trending-memes?window=30d"]}>
      <TrendingMemesPage memesApi={makeMockApi({ getTrendingMemes })} /></MemoryRouter>)
    expect(await screen.findByText("Nothing trending in the last 30 days")).toBeInTheDocument()
  })

  it("error shows a Retry button that reloads", async () => {
    const user = userEvent.setup()
    const getTrendingMemes = vi.fn()
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValueOnce(page([item("a", 4)]))
    setup({ getTrendingMemes })
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load trending memes.")
    await user.click(screen.getByRole("button", { name: "Retry" }))
    expect(await findCardImg("a")).toBeInTheDocument()
  })

  it("shows an error and no cards when the first request fails", async () => {
    setup({ getTrendingMemes: vi.fn().mockRejectedValue(new Error("boom")) })
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load trending memes")
    expect(screen.queryAllByRole("img")).toHaveLength(0)
  })

  it("Retry after a failed first request loads the first page again", async () => {
    const user = userEvent.setup()
    const getTrendingMemes = vi.fn()
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValueOnce(page([item("a", 4)]))
    setup({ getTrendingMemes })
    await user.click(await screen.findByRole("button", { name: "Retry" }))
    expect(await findCardImg("a")).toBeInTheDocument()
    expect(getTrendingMemes.mock.calls[1][1]).toBeUndefined()
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
  })

  it("a failed Show more keeps the cards and Retry resumes from the same cursor", async () => {
    const user = userEvent.setup()
    const getTrendingMemes = vi.fn()
      .mockResolvedValueOnce(page([item("a", 5)], "c1"))
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValueOnce(page([item("b", 3)]))
    setup({ getTrendingMemes })
    await user.click(await screen.findByRole("button", { name: "Show more" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load trending memes")
    expect(getCardImg("a")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Retry" }))
    await findCardImg("b")
    expect(getTrendingMemes.mock.calls[2][1]).toBe("c1")
    expect(getCardImg("a")).toBeInTheDocument()
  })

  it("opens the details modal on card click", async () => {
    const user = userEvent.setup()
    setup({ getTrendingMemes: vi.fn().mockResolvedValue(page([item("a", 1)])) })
    await user.click(await findCardImg("a"))
    expect(await screen.findByText("Meme Details")).toBeInTheDocument()
  })

  it("makes one first request under StrictMode", async () => {
    const getTrendingMemes = vi.fn().mockResolvedValue(page([item("a", 1)]))
    setup({ getTrendingMemes }, true)
    await findCardImg("a")
    await waitFor(() => expect(getTrendingMemes).toHaveBeenCalledTimes(1))
    expect(queryAllCardImgs("a")).toHaveLength(1)
  })
})

function monthItem(id: string, popularity: number, windowGain: number) {
  return { ...item(id, popularity), windowGain }
}
function setupAt(url: string, overrides: Parameters<typeof makeMockApi>[0], strict = false) {
  const api = makeMockApi(overrides)
  const ui = <MemoryRouter initialEntries={[url]}><TrendingMemesPage memesApi={api} /></MemoryRouter>
  render(strict ? <StrictMode>{ui}</StrictMode> : ui)
  return api
}
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(res => { resolve = res })
  return { promise, resolve }
}

describe("TrendingMemesPage window switch", () => {
  it("defaults to All time and requests window all", async () => {
    const getTrendingMemes = vi.fn().mockResolvedValue(page([item("a", 3)]))
    setup({ getTrendingMemes })
    await findCardImg("a")
    expect(getTrendingMemes.mock.calls[0][2]).toBe("all")
    expect(screen.getByRole("button", { name: "All time" })).toHaveAttribute("aria-pressed", "true")
    expect(screen.getByRole("button", { name: "Last 30 days" })).toHaveAttribute("aria-pressed", "false")
    expect(screen.queryByText(/in 30 days/)).not.toBeInTheDocument()
  })

  it("choosing Last 30 days clears the old cards, requests 30d once and shows gain badges", async () => {
    const user = userEvent.setup()
    const month = deferred<TrendingMemesResponse>()
    const getTrendingMemes = vi.fn()
      .mockResolvedValueOnce(page([item("a", 9)], "all-cursor"))
      .mockReturnValueOnce(month.promise)
    setup({ getTrendingMemes })
    await findCardImg("a")
    await user.click(screen.getByRole("button", { name: "Last 30 days" }))
    expect(queryCardImg("a")).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Show more" })).not.toBeInTheDocument()
    month.resolve(page([monthItem("m", 20, 5)]))
    expect(await findCardImg("m")).toBeInTheDocument()
    expect(screen.getByText("+5 in 30 days")).toBeInTheDocument()
    expect(screen.getByText("Popularity 20")).toBeInTheDocument()
    expect(getTrendingMemes).toHaveBeenCalledTimes(2)
    expect(getTrendingMemes.mock.calls[1][1]).toBeUndefined()
    expect(getTrendingMemes.mock.calls[1][2]).toBe("30d")
    expect(screen.getByRole("button", { name: "Last 30 days" })).toHaveAttribute("aria-pressed", "true")
  })

  it("opening ?window=30d loads the month view directly", async () => {
    const getTrendingMemes = vi.fn().mockResolvedValue(page([monthItem("m", 8, 2)]))
    setupAt("/trending-memes?window=30d", { getTrendingMemes })
    expect(await screen.findByText("+2 in 30 days")).toBeInTheDocument()
    expect(getTrendingMemes).toHaveBeenCalledTimes(1)
    expect(getTrendingMemes.mock.calls[0][2]).toBe("30d")
  })

  it("an unknown window value means all time", async () => {
    const getTrendingMemes = vi.fn().mockResolvedValue(page([item("a", 1)]))
    setupAt("/trending-memes?window=7d", { getTrendingMemes })
    await findCardImg("a")
    expect(getTrendingMemes.mock.calls[0][2]).toBe("all")
  })

  it("choosing All time again goes back and requests window all", async () => {
    const user = userEvent.setup()
    const getTrendingMemes = vi.fn()
      .mockResolvedValueOnce(page([monthItem("m", 8, 2)]))
      .mockResolvedValueOnce(page([item("a", 4)]))
    setupAt("/trending-memes?window=30d", { getTrendingMemes })
    await findCardImg("m")
    await user.click(screen.getByRole("button", { name: "All time" }))
    expect(await findCardImg("a")).toBeInTheDocument()
    expect(queryCardImg("m")).not.toBeInTheDocument()
    expect(getTrendingMemes.mock.calls[1][2]).toBe("all")
    expect(screen.queryByText(/in 30 days/)).not.toBeInTheDocument()
  })

  it("ignores the response of a superseded window", async () => {
    const user = userEvent.setup()
    const first = deferred<TrendingMemesResponse>()
    const second = deferred<TrendingMemesResponse>()
    const getTrendingMemes = vi.fn()
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise)
    setup({ getTrendingMemes })
    await user.click(screen.getByRole("button", { name: "Last 30 days" }))
    second.resolve(page([monthItem("m", 8, 2)]))
    await findCardImg("m")
    first.resolve(page([item("old", 99)]))
    await new Promise(resolve => setTimeout(resolve, 0))
    expect(queryCardImg("old")).not.toBeInTheDocument()
    expect(getCardImg("m")).toBeInTheDocument()
  })

  it("Show more in the month view passes the month cursor and window", async () => {
    const user = userEvent.setup()
    const getTrendingMemes = vi.fn()
      .mockResolvedValueOnce(page([monthItem("m1", 8, 3)], "mc1"))
      .mockResolvedValueOnce(page([monthItem("m2", 6, 2)]))
    setupAt("/trending-memes?window=30d", { getTrendingMemes })
    await user.click(await screen.findByRole("button", { name: "Show more" }))
    await findCardImg("m2")
    expect(getTrendingMemes.mock.calls[1][1]).toBe("mc1")
    expect(getTrendingMemes.mock.calls[1][2]).toBe("30d")
    expect(getCardImg("m1")).toBeInTheDocument()
  })

  it("Retry works in the month view", async () => {
    const user = userEvent.setup()
    const getTrendingMemes = vi.fn()
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValueOnce(page([monthItem("m", 8, 2)]))
    setupAt("/trending-memes?window=30d", { getTrendingMemes })
    await user.click(await screen.findByRole("button", { name: "Retry" }))
    expect(await findCardImg("m")).toBeInTheDocument()
    expect(getTrendingMemes.mock.calls[1][1]).toBeUndefined()
    expect(getTrendingMemes.mock.calls[1][2]).toBe("30d")
  })

  it("shows the month empty state", async () => {
    setupAt("/trending-memes?window=30d", { getTrendingMemes: vi.fn().mockResolvedValue(page([])) })
    expect(await screen.findByText("Nothing trending in the last 30 days")).toBeInTheDocument()
    expect(screen.queryByText("Nothing trending yet")).not.toBeInTheDocument()
  })

  it.each(["/trending-memes", "/trending-memes?window=30d"])(
    "makes one first request under StrictMode starting at %s", async url => {
      const getTrendingMemes = vi.fn().mockResolvedValue(page([item("a", 1)]))
      setupAt(url, { getTrendingMemes }, true)
      await findCardImg("a")
      await waitFor(() => expect(getTrendingMemes).toHaveBeenCalledTimes(1))
    },
  )

  it("makes one request per window when switching under StrictMode", async () => {
    const user = userEvent.setup()
    const getTrendingMemes = vi.fn().mockResolvedValue(page([item("a", 1)]))
    setup({ getTrendingMemes }, true)
    await findCardImg("a")
    await user.click(screen.getByRole("button", { name: "Last 30 days" }))
    await waitFor(() => expect(getTrendingMemes).toHaveBeenCalledTimes(2))
    expect(getTrendingMemes.mock.calls.map(call => call[2])).toEqual(["all", "30d"])
  })
})

describe("nav", () => {
  it("has a Trending memes link distinct from Trends", async () => {
    render(<MemoryRouter><AppLayout /></MemoryRouter>)
    await userEvent.click(screen.getByRole("button", { name: /^Find/ }))
    expect(screen.getByRole("menuitem", { name: "Trending memes" })).toHaveAttribute("href", "/trending-memes")
    expect(screen.getByRole("menuitem", { name: "Trends" })).toHaveAttribute("href", "/trends")
  })
})
