import { StrictMode } from "react"
import { act, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { makeMockApi } from "../test/mockApi"
import type { Meme } from "../types/generated/all"
import { EXAMPLES_KEY } from "./examples"
import { RECENT_SEARCHES_KEY, readRecentSearches } from "./recentSearches"
import { SearchHero } from "./SearchHero"

function SearchStub() {
  const location = useLocation()
  return <div data-testid="search-location">{`search:${location.search}`}</div>
}

const tagged = (id: string, ...names: string[]): Meme => ({
  id, imageUrl: `/i/${id}`, text: [], flagged: false, tags: names.map(name => ({ name })),
})
const threeItems = { items: [tagged("a"), tagged("b"), tagged("c")], hasNext: false }
const SOURCE = [tagged("1", "cats", "monday", "work", "pizza"), tagged("2", "cats", "monday", "work")]

function setup(props: { candidatesSource?: Meme[] | null; memesApi?: ReturnType<typeof makeMockApi>; strict?: boolean } = {}) {
  const memesApi = props.memesApi ?? makeMockApi({ searchMemes: vi.fn().mockResolvedValue(threeItems) })
  const tree = (
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route path="/" element={<SearchHero memesApi={memesApi} candidatesSource={props.candidatesSource ?? null} />} />
        <Route path="/search" element={<SearchStub />} />
      </Routes>
    </MemoryRouter>
  )
  render(props.strict ? <StrictMode>{tree}</StrictMode> : tree)
  return { memesApi }
}

function mockPointerFine(matches: boolean) {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: matches && query === "(pointer: fine)",
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  })
}

describe("SearchHero", () => {
  beforeEach(() => {
    localStorage.clear()
    sessionStorage.clear()
  })
  afterEach(() => {
    vi.restoreAllMocks()
    // @ts-expect-error remove the test double again
    delete window.matchMedia
  })

  it("has one heading, the orientation line and an accessible search form", () => {
    setup()
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1)
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Find the right meme for any moment")
    expect(
      screen.getByText("Search the meme library by the words on a meme, what it shows, or a feeling."),
    ).toBeInTheDocument()
    expect(screen.getByRole("search")).toBeInTheDocument()
    const input = screen.getByRole("searchbox", { name: "Search memes" })
    expect(input).toHaveAttribute("name", "q")
    expect(input).toHaveClass("text-base", "min-h-12")
  })

  it.each([
    ["cat", "?q=cat"],
    ["a&b c", "?q=a%26b%20c"],
    ["#1 мем", `?q=${encodeURIComponent("#1 мем")}`],
    ["   ", ""],
    ["", ""],
  ])("submitting %j goes to /search%s", async (typed, search) => {
    setup()
    const input = screen.getByRole("searchbox", { name: "Search memes" })
    if (typed) await userEvent.type(input, typed)
    await userEvent.type(input, "{Enter}")
    expect(await screen.findByTestId("search-location")).toHaveTextContent(`search:${search}`)
  })

  it("remembers a non-blank submitted search and not a blank one", async () => {
    setup()
    await userEvent.type(screen.getByRole("searchbox"), "pizza{Enter}")
    expect(readRecentSearches()).toEqual(["pizza"])
  })

  it("does not remember a blank submit", async () => {
    setup()
    await userEvent.type(screen.getByRole("searchbox"), "  {Enter}")
    expect(readRecentSearches()).toEqual([])
  })

  it("the Search button submits too", async () => {
    setup()
    await userEvent.type(screen.getByRole("searchbox"), "dogs")
    await userEvent.click(screen.getByRole("button", { name: "Search" }))
    expect(await screen.findByTestId("search-location")).toHaveTextContent("search:?q=dogs")
  })

  it("shows up to three verified example chips linking to the search", async () => {
    const { memesApi } = setup({ candidatesSource: SOURCE })
    const chip = await screen.findByRole("link", { name: "cats" })
    expect(chip).toHaveAttribute("href", "/search?q=cats")
    expect(screen.getByText("Try:")).toBeInTheDocument()
    expect(screen.getAllByRole("link")).toHaveLength(3)
    expect(within(screen.getByRole("group", { name: "Example searches" })).getAllByRole("link")).toHaveLength(3)
    expect(memesApi.searchMemes).toHaveBeenCalledWith({ query: "cats", limit: 3, record: false })
  })

  it("shows no chips and no label when fewer than two examples verify", async () => {
    const searchMemes = vi.fn().mockResolvedValue({ items: [], hasNext: false })
    setup({ candidatesSource: SOURCE, memesApi: makeMockApi({ searchMemes }) })
    await waitFor(() => expect(searchMemes).toHaveBeenCalled())
    await waitFor(() => expect(screen.queryByTestId("chips-skeleton")).not.toBeInTheDocument())
    expect(screen.queryByText("Try:")).not.toBeInTheDocument()
    expect(screen.queryAllByRole("link")).toHaveLength(0)
  })

  it("reserves the chip row with a skeleton while verifying and keeps the input usable", async () => {
    const searchMemes = vi.fn().mockReturnValue(new Promise(() => {}))
    setup({ candidatesSource: SOURCE, memesApi: makeMockApi({ searchMemes }) })
    expect(screen.getByTestId("chips-skeleton")).toBeInTheDocument()
    await userEvent.type(screen.getByRole("searchbox"), "ok")
    expect(screen.getByRole("searchbox")).toHaveValue("ok")
  })

  it("uses a valid sessionStorage cache without probing", () => {
    sessionStorage.setItem(EXAMPLES_KEY, JSON.stringify(["owls", "bees"]))
    const { memesApi } = setup({ candidatesSource: SOURCE })
    expect(screen.getByRole("link", { name: "owls" })).toHaveAttribute("href", "/search?q=owls")
    expect(memesApi.searchMemes).not.toHaveBeenCalled()
  })

  it("replaces the examples with the last searches, and Clear removes them", async () => {
    localStorage.setItem(RECENT_SEARCHES_KEY, JSON.stringify(["pizza", "tacos"]))
    const { memesApi } = setup({ candidatesSource: SOURCE })
    expect(screen.getByText("Your last searches")).toBeInTheDocument()
    const group = screen.getByRole("group", { name: "Your last searches" })
    expect(within(group).getAllByRole("link")).toHaveLength(2)
    expect(screen.getByRole("link", { name: "pizza" })).toHaveAttribute("href", "/search?q=pizza")
    expect(screen.queryByText("Try:")).not.toBeInTheDocument()
    await act(async () => {})
    await userEvent.click(screen.getByRole("button", { name: "Clear last searches" }))
    expect(screen.queryByText("Your last searches")).not.toBeInTheDocument()
    expect(readRecentSearches()).toEqual([])
    expect(memesApi).toBeDefined()
  })

  it("makes no example probes while the last searches are shown", async () => {
    localStorage.setItem(RECENT_SEARCHES_KEY, JSON.stringify(["pizza"]))
    const { memesApi } = setup({ candidatesSource: SOURCE })
    await act(async () => {})
    expect(memesApi.searchMemes).not.toHaveBeenCalled()
  })

  it("remembers a negative verification for the tab and probes nothing on the next visit", async () => {
    const searchMemes = vi.fn().mockResolvedValue({ items: [], hasNext: false })
    const first = render(
      <MemoryRouter>
        <SearchHero memesApi={makeMockApi({ searchMemes })} candidatesSource={SOURCE} />
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.queryByTestId("chips-skeleton")).not.toBeInTheDocument())
    const probes = searchMemes.mock.calls.length
    expect(probes).toBe(4)
    first.unmount()
    render(
      <MemoryRouter>
        <SearchHero memesApi={makeMockApi({ searchMemes })} candidatesSource={SOURCE} />
      </MemoryRouter>,
    )
    await act(async () => {})
    expect(searchMemes).toHaveBeenCalledTimes(probes)
    expect(screen.queryByTestId("chips-skeleton")).not.toBeInTheDocument()
  })

  it("does not probe again when the candidates go away and come back", async () => {
    const searchMemes = vi.fn().mockResolvedValue(threeItems)
    const memesApi = makeMockApi({ searchMemes })
    const view = render(
      <MemoryRouter>
        <SearchHero memesApi={memesApi} candidatesSource={SOURCE} />
      </MemoryRouter>,
    )
    await screen.findByRole("link", { name: "cats" })
    const probes = searchMemes.mock.calls.length
    view.rerender(
      <MemoryRouter>
        <SearchHero memesApi={memesApi} candidatesSource={null} />
      </MemoryRouter>,
    )
    view.rerender(
      <MemoryRouter>
        <SearchHero memesApi={memesApi} candidatesSource={[...SOURCE]} />
      </MemoryRouter>,
    )
    await act(async () => {})
    expect(searchMemes).toHaveBeenCalledTimes(probes)
  })

  it("shortens only the display text of a long last search; the link keeps the full query", () => {
    const long = "word ".repeat(30).trim()
    localStorage.setItem(RECENT_SEARCHES_KEY, JSON.stringify([long]))
    setup()
    const chip = screen.getByRole("link", { name: long })
    expect(chip).toHaveAttribute("href", `/search?q=${encodeURIComponent(long)}`)
    expect(chip).toHaveAttribute("title", long)
    expect(chip.textContent?.endsWith("…")).toBe(true)
    expect(chip.textContent?.length).toBeLessThan(long.length)
  })

  it("renders without the row when localStorage throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked")
    })
    setup()
    expect(screen.getByRole("searchbox")).toBeInTheDocument()
    expect(screen.queryByText("Your last searches")).not.toBeInTheDocument()
  })

  it("focuses the input on a fine pointer only", () => {
    mockPointerFine(true)
    setup()
    expect(screen.getByRole("searchbox")).toHaveFocus()
  })

  it("does not focus the input on a coarse pointer", () => {
    mockPointerFine(false)
    setup()
    expect(screen.getByRole("searchbox")).not.toHaveFocus()
  })

  it("survives a StrictMode double mount", async () => {
    setup({ candidatesSource: SOURCE, strict: true })
    expect(await screen.findByRole("link", { name: "cats" })).toBeInTheDocument()
    expect(screen.getAllByRole("link")).toHaveLength(3)
  })
})
