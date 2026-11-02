import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, useLocation } from "react-router-dom"
import DiscoveryPage from "./DiscoveryPage"
import {
  makeDiscoveryMemes, makeDiscoveryMisses, makeDiscoveryQueries, makeMemeItem, makeMissItem, makeQueryItem,
} from "../discovery/fixtures"
import { DiscoveryRequestError } from "../discovery/types"
import { makeMockApi } from "../test/mockApi"

function Where() {
  const location = useLocation()
  return <output data-testid="where">{location.search}</output>
}

function setup(overrides: Parameters<typeof makeMockApi>[0] = {}, entry = "/admin/discovery") {
  const api = makeMockApi(overrides)
  render(
    <MemoryRouter initialEntries={[entry]}>
      <DiscoveryPage memesApi={api} />
      <Where />
    </MemoryRouter>,
  )
  return api
}

describe("DiscoveryPage", () => {
  it("shows the heading and three tabs with Memes selected", async () => {
    setup()
    expect(screen.getByRole("heading", { name: "Discovery report" })).toBeInTheDocument()
    const tabs = screen.getAllByRole("tab")
    expect(tabs.map((t) => t.textContent)).toEqual(["Memes", "Queries", "Misses"])
    expect(screen.getByRole("tab", { name: "Memes" })).toHaveAttribute("aria-selected", "true")
    await screen.findByText("quokka.jpg")
  })

  it("switches tab, records it in the URL and calls the matching method once", async () => {
    const user = userEvent.setup()
    const api = setup()
    await screen.findByText("quokka.jpg")
    await user.click(screen.getByRole("tab", { name: "Queries" }))
    expect(screen.getByTestId("where")).toHaveTextContent("view=queries")
    await screen.findByText("funny cat")
    expect(api.getDiscoveryQueries).toHaveBeenCalledTimes(1)
    await user.click(screen.getByRole("tab", { name: "Misses" }))
    await screen.findByText("lost quokka")
    expect(api.getDiscoveryMisses).toHaveBeenCalledTimes(1)
  })

  it("opens on the tab named in the URL and falls back to Memes for an unknown one", async () => {
    setup({}, "/admin/discovery?view=misses")
    await screen.findByText("lost quokka")
    expect(screen.getByRole("tab", { name: "Misses" })).toHaveAttribute("aria-selected", "true")
  })

  it("falls back to the Memes tab for an unknown view", async () => {
    setup({}, "/admin/discovery?view=nope")
    await screen.findByText("quokka.jpg")
    expect(screen.getByRole("tab", { name: "Memes" })).toHaveAttribute("aria-selected", "true")
  })

  describe("Memes", () => {
    it("asks with the default sort and size and no source filter", async () => {
      const api = setup()
      await screen.findByText("quokka.jpg")
      const params = vi.mocked(api.getDiscoveryMemes).mock.calls[0][0]
      expect(params.limit).toBe(50)
      expect(params.sort).toBe("per_100")
      expect(params.sources).toBeUndefined()
    })

    it("shows the thumbnail link, counts, sources and the ratio", async () => {
      setup()
      const image = await screen.findByAltText("quokka.jpg")
      expect(image.closest("a")).toHaveAttribute("href", "/memes/m1")
      expect(image).toHaveAttribute("src", "http://example.com/api/images/m1")
      expect(screen.getByText("40.0")).toBeInTheDocument()
      expect(screen.getByText(/hash_cross_corpus: 3/)).toBeInTheDocument()
    })

    it("shows ∞ for a meme that was re-dropped and never shown", async () => {
      setup({
        getDiscoveryMemes: vi.fn().mockResolvedValue(
          makeDiscoveryMemes({ items: [makeMemeItem({ per100: null, per100Infinite: true })] })),
      })
      expect(await screen.findByText("∞")).toBeInTheDocument()
    })

    it("explains missing impression data and shows n/a", async () => {
      setup({
        getDiscoveryMemes: vi.fn().mockResolvedValue(makeDiscoveryMemes({
          impressionsAvailable: false,
          items: [makeMemeItem({ impressions: null, impressionsWindow: null, per100: null })],
        })),
      })
      expect(await screen.findByText(/impressions are n\/a/)).toBeInTheDocument()
      expect(screen.getAllByText("n/a").length).toBeGreaterThanOrEqual(3)
    })

    it("refetches with the chosen sort and size", async () => {
      const user = userEvent.setup()
      const api = setup()
      await screen.findByText("quokka.jpg")
      await user.selectOptions(screen.getByLabelText("Sort"), "saves")
      await waitFor(() => expect(vi.mocked(api.getDiscoveryMemes).mock.calls.at(-1)![0].sort).toBe("saves"))
      await user.selectOptions(screen.getByLabelText("Show top"), "100")
      await waitFor(() => expect(vi.mocked(api.getDiscoveryMemes).mock.calls.at(-1)![0].limit).toBe(100))
    })

    it("sends only the checked sources", async () => {
      const user = userEvent.setup()
      const api = setup()
      await screen.findByText("quokka.jpg")
      await user.click(screen.getByLabelText("recut"))
      await waitFor(() => {
        const sources = vi.mocked(api.getDiscoveryMemes).mock.calls.at(-1)![0].sources
        expect(sources).toBeDefined()
        expect(sources).not.toContain("recut")
        expect(sources).toContain("hash_cross_corpus")
      })
    })

    it("shows the server's message on a 422 with a Retry that refetches", async () => {
      const user = userEvent.setup()
      const api = setup({
        getDiscoveryMemes: vi.fn()
          .mockRejectedValueOnce(new DiscoveryRequestError(422, "bad"))
          .mockResolvedValue(makeDiscoveryMemes()),
      })
      const alert = await screen.findByRole("alert")
      expect(alert).toHaveTextContent("bad")
      await user.click(within(alert).getByRole("button", { name: "Retry" }))
      await screen.findByText("quokka.jpg")
      expect(api.getDiscoveryMemes).toHaveBeenCalledTimes(2)
    })
  })

  describe("Queries", () => {
    async function openQueries(items = [makeQueryItem()]) {
      const user = userEvent.setup()
      const api = setup({ getDiscoveryQueries: vi.fn().mockResolvedValue(makeDiscoveryQueries(items)) },
        "/admin/discovery?view=queries")
      return { user, api }
    }

    it("links a search row to the search page with its facets", async () => {
      await openQueries()
      const link = await screen.findByRole("link", { name: "Search this" })
      expect(link).toHaveAttribute("href", "/search?q=funny+cat&topic=work")
      expect(screen.getByText("topic:work")).toBeInTheDocument()
    })

    it("has no link for a meme_text row", async () => {
      await openQueries([makeQueryItem({ kind: "meme_text" })])
      await screen.findByText("funny cat")
      expect(screen.queryByRole("link", { name: "Search this" })).toBeNull()
    })

    it("shows (no text) for a facet-only search and links to its tags", async () => {
      await openQueries([makeQueryItem({ query: "", tags: ["animal:cat"] })])
      expect(await screen.findByText("(no text)")).toBeInTheDocument()
      expect(screen.getByRole("link", { name: "Search this" })).toHaveAttribute("href", "/search?animal=cat")
    })

    it("has no link for a row with neither text nor tags", async () => {
      await openQueries([makeQueryItem({ query: "", tags: [] })])
      await screen.findByText("(no text)")
      expect(screen.queryByRole("link", { name: "Search this" })).toBeNull()
    })

    it("refetches with includeDegraded", async () => {
      const { user, api } = await openQueries()
      await screen.findByText("funny cat")
      await user.click(screen.getByLabelText("Include degraded"))
      await waitFor(() => expect(vi.mocked(api.getDiscoveryQueries).mock.calls.at(-1)![0].includeDegraded).toBe(true))
    })
  })

  describe("Misses", () => {
    function openMisses(overrides: Parameters<typeof makeDiscoveryMisses>[0] = {}) {
      const api = setup({ getDiscoveryMisses: vi.fn().mockResolvedValue(makeDiscoveryMisses(overrides)) },
        "/admin/discovery?view=misses")
      return api
    }

    it("shows the cut-off date, a row and its link", async () => {
      openMisses()
      expect(await screen.findByText(/Outcome data since 2026-09-01/)).toBeInTheDocument()
      expect(screen.getByText("lost quokka")).toBeInTheDocument()
      expect(screen.getByRole("link", { name: "Search this" })).toHaveAttribute("href", "/search?q=lost+quokka")
    })

    it("says the migration has not run when the schema is missing", async () => {
      openMisses({ status: "no_schema", cutoff: null, items: [] })
      expect(await screen.findByText(/the S6 migration has not run here yet/)).toBeInTheDocument()
      expect(screen.queryByText("lost quokka")).toBeNull()
    })

    it("says there is no outcome data yet", async () => {
      openMisses({ status: "no_outcomes", cutoff: null, items: [] })
      expect(await screen.findByText(/no linked save or press/)).toBeInTheDocument()
    })

    it("refetches with explicitOnly", async () => {
      const user = userEvent.setup()
      const api = openMisses({ items: [makeMissItem()] })
      await screen.findByText("lost quokka")
      await user.click(screen.getByLabelText("Explicit only"))
      await waitFor(() => expect(vi.mocked(api.getDiscoveryMisses).mock.calls.at(-1)![0].explicitOnly).toBe(true))
    })
  })

  it("gives every control an accessible name and a touch-sized height", async () => {
    setup()
    await screen.findByText("quokka.jpg")
    const controls = [...screen.getAllByRole("combobox"), ...screen.getAllByRole("spinbutton"),
      ...screen.getAllByRole("checkbox")]
    expect(controls.length).toBeGreaterThan(4)
    for (const control of controls) {
      expect(control).toHaveAccessibleName()
      expect(control.closest("label")).toHaveClass("min-h-11")
    }
    for (const tab of screen.getAllByRole("tab")) expect(tab).toHaveClass("min-h-11")
  })
})
