import { StrictMode } from "react"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { FeaturesContext } from "../features/useFeature"
import { makeMockApi } from "../test/mockApi"
import type { Meme } from "../types/generated/all"
import HomePage from "./HomePage"

const meme = (id: string, extra: Partial<Meme> = {}): Meme => ({
  id, imageUrl: `/i/${id}`, text: [], tags: [], flagged: false, ...extra,
})
const memes = (n: number, prefix = "m") => Array.from({ length: n }, (_, i) => meme(`${prefix}${i}`))
const trending = (items: Meme[]) => ({ items: items.map(m => ({ meme: m, popularity: 3 })), hasNext: false })
const recentFeed = (items: Meme[]) => ({ items, hasNext: false })

function Where() {
  const location = useLocation()
  return <div data-testid="where">{location.pathname + location.search}</div>
}

function renderHome(
  api: ReturnType<typeof makeMockApi>,
  options: { prod?: boolean; strict?: boolean } = {},
) {
  let tree = (
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route path="/" element={<HomePage memesApi={api} />} />
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>
  )
  if (options.prod) {
    tree = <FeaturesContext.Provider value={{ stage: "prod", capabilities: null }}>{tree}</FeaturesContext.Provider>
  }
  return render(options.strict ? <StrictMode>{tree}</StrictMode> : tree)
}

function apiWith(recent: Meme[], trend: Meme[] = []) {
  return makeMockApi({
    searchMemes: vi.fn().mockImplementation(async (request: { query?: string }) =>
      request.query === undefined ? recentFeed(recent) : recentFeed(memes(3, "probe")),
    ),
    getTrendingMemes: vi.fn().mockResolvedValue(trending(trend)),
    getStatistics: vi.fn(),
  })
}

describe("HomePage", () => {
  beforeEach(() => {
    localStorage.clear()
    sessionStorage.clear()
  })
  afterEach(() => vi.restoreAllMocks())

  it("has one h1 and both rails with See all links", async () => {
    renderHome(apiWith(memes(12), memes(5, "t")))
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1)
    expect(await screen.findByRole("heading", { level: 2, name: "Recently added" })).toBeInTheDocument()
    expect(await screen.findByRole("heading", { level: 2, name: "Trending" })).toBeInTheDocument()
    expect(screen.getByText("Memes that keep coming back.")).toBeInTheDocument()
    const links = screen.getAllByRole("link", { name: /see all/i })
    expect(links.map(l => l.getAttribute("href"))).toEqual(["/explore", "/trending-memes"])
  })

  it("drops trending memes that are also in recent", async () => {
    const recent = memes(12)
    const api = apiWith(recent, [recent[0], ...memes(3, "t")])
    renderHome(api)
    const trendingRegion = await screen.findByRole("region", { name: "Trending" })
    await waitFor(() => expect(within(trendingRegion).getAllByTestId("card-root")).toHaveLength(3))
  })

  it("hides Trending without a message when fewer than three memes remain", async () => {
    const recent = memes(12)
    renderHome(apiWith(recent, [recent[0], recent[1], ...memes(1, "t")]))
    await screen.findByRole("heading", { name: "Recently added" })
    await waitFor(() => expect(screen.queryByRole("region", { busy: true })).not.toBeInTheDocument())
    expect(screen.queryByRole("heading", { name: "Trending" })).not.toBeInTheDocument()
    expect(screen.queryByRole("status")).not.toBeInTheDocument()
  })

  it("shows the empty state with an upload link on dev when both feeds are empty", async () => {
    renderHome(apiWith([], []))
    expect(await screen.findByText("No memes yet.")).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "Upload memes" })).toHaveAttribute("href", "/upload")
  })

  it("shows a plain message and no upload link on prod when both feeds are empty", async () => {
    renderHome(apiWith([], []), { prod: true })
    expect(await screen.findByText("Nothing here yet, check back soon.")).toBeInTheDocument()
    expect(screen.queryByRole("link", { name: "Upload memes" })).not.toBeInTheDocument()
  })

  it("lets recent fail alone and retries only it", async () => {
    const searchMemes = vi
      .fn()
      .mockRejectedValueOnce(new Error("down"))
      .mockResolvedValue(recentFeed(memes(12)))
    const api = makeMockApi({
      searchMemes,
      getTrendingMemes: vi.fn().mockResolvedValue(trending(memes(5, "t"))),
    })
    renderHome(api)
    expect(await screen.findByText("Could not load recent memes.")).toBeInTheDocument()
    expect(await screen.findByRole("heading", { name: "Trending" })).toBeInTheDocument()
    expect(screen.queryByText("Could not load trending memes.")).not.toBeInTheDocument()
    const feedCalls = () => searchMemes.mock.calls.filter(([r]) => r.query === undefined).length
    expect(feedCalls()).toBe(1)
    await userEvent.click(screen.getByRole("button", { name: "Try again" }))
    await waitFor(() => expect(screen.getAllByTestId("card-root").length).toBeGreaterThan(0))
    expect(feedCalls()).toBe(2)
    expect(api.getTrendingMemes).toHaveBeenCalledTimes(1)
  })

  it("shows two independent errors when the backend is down and the search still navigates", async () => {
    const api = makeMockApi({
      searchMemes: vi.fn().mockRejectedValue(new Error("down")),
      getTrendingMemes: vi.fn().mockRejectedValue(new Error("down")),
    })
    renderHome(api)
    expect(await screen.findByText("Could not load recent memes.")).toBeInTheDocument()
    expect(await screen.findByText("Could not load trending memes.")).toBeInTheDocument()
    expect(screen.getAllByRole("button", { name: "Try again" })).toHaveLength(2)
    await userEvent.type(screen.getByRole("searchbox"), "cat{Enter}")
    expect(await screen.findByTestId("where")).toHaveTextContent("/search?q=cat")
  })

  it("shows skeletons with busy rails while loading and keeps the input usable", async () => {
    const api = makeMockApi({
      searchMemes: vi.fn().mockReturnValue(new Promise(() => {})),
      getTrendingMemes: vi.fn().mockReturnValue(new Promise(() => {})),
    })
    renderHome(api)
    expect(screen.getAllByTestId("meme-card-skeleton").length).toBeGreaterThan(0)
    expect(screen.getAllByRole("region", { busy: true })).toHaveLength(2)
    await userEvent.type(screen.getByRole("searchbox"), "abc")
    expect(screen.getByRole("searchbox")).toHaveValue("abc")
  })

  it("never shows flagged memes", async () => {
    const recent = [meme("flagged-one", { flagged: true }), ...memes(3)]
    renderHome(apiWith(recent))
    await waitFor(() => expect(screen.getAllByTestId("card-root")).toHaveLength(3))
    expect(document.querySelector('a[href="/memes/flagged-one"]')).toBeNull()
  })

  it("opens a meme page when a card is clicked", async () => {
    renderHome(apiWith(memes(3)))
    const links = await screen.findAllByRole("link", { name: "Meme" })
    await userEvent.click(links[1])
    expect(await screen.findByTestId("where")).toHaveTextContent("/memes/m1")
  })

  it("shows the Reply card on dev and not on prod", async () => {
    const { unmount } = renderHome(apiWith(memes(3)))
    expect(screen.getByRole("link", { name: "Open Reply" })).toBeInTheDocument()
    unmount()
    renderHome(apiWith(memes(3)), { prod: true })
    await screen.findByRole("heading", { name: "Recently added" })
    expect(screen.queryByRole("link", { name: "Open Reply" })).not.toBeInTheDocument()
  })

  it("never calls a dev-only endpoint", async () => {
    const api = apiWith(memes(12), memes(5, "t"))
    renderHome(api)
    await screen.findByRole("heading", { name: "Trending" })
    expect(api.getStatistics).not.toHaveBeenCalled()
  })

  it("under StrictMode shows one set of cards per rail", async () => {
    const api = apiWith(memes(12), memes(5, "t"))
    renderHome(api, { strict: true })
    const region = await screen.findByRole("region", { name: "Recently added" })
    await waitFor(() => expect(within(region).getAllByTestId("card-root")).toHaveLength(12))
    const feedCalls = vi.mocked(api.searchMemes).mock.calls.filter(([r]) => r.query === undefined).length
    expect(feedCalls).toBeLessThanOrEqual(2)
    expect(vi.mocked(api.getTrendingMemes).mock.calls.length).toBeLessThanOrEqual(2)
  })
})
