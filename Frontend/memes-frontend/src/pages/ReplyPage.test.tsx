import { act, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { StrictMode } from "react"
import { MemoryRouter } from "react-router-dom"
import ReplyPage from "./ReplyPage"
import { makeMockApi } from "../test/mockApi"
import { TEST_TAXONOMY, makeProfile, makeResponse, makeResult } from "../reply/fixtures"
import { MessageSearchFailedError } from "../api/errors"

beforeEach(() => window.sessionStorage.clear())

function renderPage(api = makeMockApi()) {
  render(<MemoryRouter><ReplyPage memesApi={api} /></MemoryRouter>)
  return api
}

async function search(user: ReturnType<typeof userEvent.setup>, text = "my manager moved the meeting") {
  await user.type(screen.getByLabelText("Message to reply to"), text)
  await user.click(screen.getByRole("button", { name: "Find a meme" }))
}

const calls = (api: ReturnType<typeof makeMockApi>) => (api.searchMemeFromText as ReturnType<typeof vi.fn>).mock.calls

describe("ReplyPage", () => {
  it("does nothing for blank input", async () => {
    const api = renderPage()
    expect(screen.getByRole("button", { name: "Find a meme" })).toBeDisabled()
    expect(api.searchMemeFromText).not.toHaveBeenCalled()
    await waitFor(() => expect(api.getTaxonomy).toHaveBeenCalled())
    await act(async () => {})
  })

  it("searches with the text only and shows the reading, the results and the result count", async () => {
    const user = userEvent.setup()
    const api = renderPage(makeMockApi({
      searchMemeFromText: vi.fn().mockResolvedValue(makeResponse({
        items: [makeResult("a"), makeResult("b")],
        facets: [{ name: "emotion", buckets: [{ value: "joy", count: 2 }] }],
      })),
    }))

    await search(user)

    await waitFor(() => expect(screen.getByRole("heading", { name: "How I read your message" })).toBeInTheDocument())
    expect(calls(api)[0][0]).toEqual({ text: "my manager moved the meeting", limit: 20 })
    expect(screen.getAllByRole("img", { name: /^[ab]$/ })).toHaveLength(2)
    expect(screen.getByText("2 memes found")).toBeInTheDocument()
    expect(screen.getByRole("group", { name: "Emotion" })).toBeInTheDocument()
  })

  it("announces a single result as '1 meme found'", async () => {
    const user = userEvent.setup()
    renderPage(makeMockApi({
      searchMemeFromText: vi.fn().mockResolvedValue(makeResponse({ items: [makeResult("a")] })),
    }))
    await search(user)
    expect(await screen.findByText("1 meme found")).toBeInTheDocument()
  })

  it("keeps results, facets and filter chips after a failed refine, and Try again keeps the filter", async () => {
    const user = userEvent.setup()
    const search_ = vi.fn()
      .mockResolvedValueOnce(makeResponse({ items: [makeResult("a")], facets: [{ name: "emotion", buckets: [{ value: "joy", count: 1 }] }] }))
      .mockRejectedValueOnce(new MessageSearchFailedError(500))
      .mockResolvedValue(makeResponse({ items: [makeResult("a")], facets: [{ name: "emotion", buckets: [{ value: "joy", count: 1 }] }] }))
    const api = renderPage(makeMockApi({ searchMemeFromText: search_ }))
    await search(user)
    await user.click(await screen.findByLabelText("Joy (1)"))

    expect(await screen.findByRole("alert")).toHaveTextContent(/something went wrong/i)
    expect(screen.getByRole("img", { name: "a" })).toBeInTheDocument()
    expect(screen.getByLabelText("Joy (1)")).toBeChecked()
    expect(screen.getByRole("button", { name: "Remove filter emotion: Joy" })).toBeInTheDocument()

    await user.click(within(screen.getByRole("alert")).getByRole("button"))
    await waitFor(() => expect(calls(api)).toHaveLength(3))
    expect(calls(api)[2][0].filters).toEqual({ emotion: ["joy"] })
    expect(calls(api)[2][0].profile).toBeDefined()
  })

  it("keeps the list after a failed Show more, hides Show more while in error, and Try again recovers", async () => {
    const user = userEvent.setup()
    const search_ = vi.fn()
      .mockResolvedValueOnce(makeResponse({ items: [makeResult("a")], hasNext: true, nextCursor: "c1" }))
      .mockRejectedValueOnce(new MessageSearchFailedError(500))
      .mockResolvedValue(makeResponse({ items: [makeResult("a")] }))
    renderPage(makeMockApi({ searchMemeFromText: search_ }))
    await search(user)
    await user.click(await screen.findByRole("button", { name: "Show more" }))

    expect(await screen.findByRole("alert")).toBeInTheDocument()
    expect(screen.getByRole("img", { name: "a" })).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Show more" })).not.toBeInTheDocument()

    await user.click(within(screen.getByRole("alert")).getByRole("button"))
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument())
  })

  it("removing a chip re-runs retrieval with the edited profile after the debounce", async () => {
    const user = userEvent.setup()
    const api = renderPage(makeMockApi({
      searchMemeFromText: vi.fn().mockResolvedValue(makeResponse({ items: [makeResult("a")] })),
    }))
    await search(user)
    await screen.findByRole("heading", { name: "How I read your message" })

    await user.click(screen.getByRole("button", { name: "Remove emotion: Annoyance" }))

    await waitFor(() => expect(calls(api)).toHaveLength(2), { timeout: 2000 })
    const request = calls(api)[1][0]
    expect(request.profile.emotion).toEqual([])
    expect(request.profile.degraded).toBe(false)
    expect(screen.getByText("Edited")).toBeInTheDocument()
  })

  it("a facet selection re-runs with the filter and the profile, immediately", async () => {
    const user = userEvent.setup()
    const api = renderPage(makeMockApi({
      searchMemeFromText: vi.fn().mockResolvedValue(makeResponse({
        items: [makeResult("a")], facets: [{ name: "emotion", buckets: [{ value: "joy", count: 1 }] }],
      })),
    }))
    await search(user)
    await user.click(await screen.findByLabelText("Joy (1)"))

    await waitFor(() => expect(calls(api)).toHaveLength(2))
    expect(calls(api)[1][0].filters).toEqual({ emotion: ["joy"] })
    expect(calls(api)[1][0].profile).toBeDefined()
  })

  it("shows the limited-understanding banner for a degraded response and keeps the reading editable", async () => {
    const user = userEvent.setup()
    renderPage(makeMockApi({
      searchMemeFromText: vi.fn().mockResolvedValue(makeResponse({
        degraded: true, fallback: "unavailable", items: [makeResult("a", { axisPath: false })],
        profile: makeProfile({ degraded: true, situation: null, emotion: [], stance: [], topic: [], tone: [] }),
      })),
    }))
    await search(user)

    expect(await screen.findByText(/Limited understanding/)).toBeInTheDocument()
    expect(screen.getByText(/couldn't read your message structurally/i)).toBeInTheDocument()
    expect(screen.getByText("Matched by words only")).toBeInTheDocument()
    expect(screen.getByLabelText("Situation")).toBeEnabled()
  })

  it("shows the unavailable state when the environment has no parser", async () => {
    const user = userEvent.setup()
    const response = makeResponse({ degraded: true, fallback: "unavailable" })
    response.meta.available = false
    renderPage(makeMockApi({ searchMemeFromText: vi.fn().mockResolvedValue(response) }))
    await search(user)
    expect(await screen.findByText("Message search isn't available in this environment.")).toBeInTheDocument()
  })

  it("shows 'No memes fit' with the active filters removable when nothing matches", async () => {
    const user = userEvent.setup()
    const search_ = vi.fn()
      .mockResolvedValueOnce(makeResponse({ items: [makeResult("a")], facets: [{ name: "emotion", buckets: [{ value: "joy", count: 1 }] }] }))
      .mockResolvedValue(makeResponse({ items: [] }))
    const api = renderPage(makeMockApi({ searchMemeFromText: search_ }))
    await search(user)
    await user.click(await screen.findByLabelText("Joy (1)"))

    expect(await screen.findByText("No memes fit")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Remove filter emotion: Joy" }))
    await waitFor(() => expect(calls(api).length).toBeGreaterThanOrEqual(3))
  })

  it("shows a retry message on failure and never echoes the message text", async () => {
    const user = userEvent.setup()
    renderPage(makeMockApi({ searchMemeFromText: vi.fn().mockRejectedValue(new MessageSearchFailedError(500)) }))
    await search(user, "SECRET-MESSAGE")

    const alert = await screen.findByRole("alert")
    expect(alert).toHaveTextContent(/something went wrong/i)
    expect(alert.textContent).not.toContain("SECRET-MESSAGE")
  })

  it("Show more appends the next page with the cursor", async () => {
    const user = userEvent.setup()
    const search_ = vi.fn()
      .mockResolvedValueOnce(makeResponse({ items: [makeResult("a")], hasNext: true, nextCursor: "c1" }))
      .mockResolvedValueOnce(makeResponse({ items: [makeResult("b")] }))
    const api = renderPage(makeMockApi({ searchMemeFromText: search_ }))
    await search(user)

    await user.click(await screen.findByRole("button", { name: "Show more" }))

    await waitFor(() => expect(screen.getAllByRole("img", { name: /^[ab]$/ })).toHaveLength(2))
    expect(calls(api)[1][0]).toEqual({ text: "my manager moved the meeting", limit: 20, cursor: "c1" })
    expect(screen.queryByRole("button", { name: "Show more" })).not.toBeInTheDocument()
  })

  it("opens the existing details modal for a result", async () => {
    const user = userEvent.setup()
    renderPage(makeMockApi({ searchMemeFromText: vi.fn().mockResolvedValue(makeResponse({ items: [makeResult("a")] })) }))
    await search(user)
    await user.click(await screen.findByRole("img", { name: "a" }))
    expect(await screen.findByText("Meme Details")).toBeInTheDocument()
  })

  it("collapses the filters on small screens behind a disclosure button", async () => {
    const user = userEvent.setup()
    renderPage(makeMockApi({
      searchMemeFromText: vi.fn().mockResolvedValue(makeResponse({
        items: [makeResult("a")], facets: [{ name: "emotion", buckets: [{ value: "joy", count: 1 }] }],
      })),
    }))
    await search(user)
    const toggle = await screen.findByRole("button", { name: /^Filters/ })
    expect(toggle).toHaveAttribute("aria-expanded", "false")
    await user.click(toggle)
    expect(toggle).toHaveAttribute("aria-expanded", "true")
    expect(within(screen.getByRole("region", { name: "Filters" })).getByLabelText("Joy (1)")).toBeInTheDocument()
  })

  it("survives StrictMode double mounting: results shown and vocabulary labels loaded", async () => {
    const user = userEvent.setup()
    const api = makeMockApi({
      searchMemeFromText: vi.fn().mockResolvedValue(makeResponse({ items: [makeResult("a")] })),
    })
    render(<StrictMode><MemoryRouter><ReplyPage memesApi={api} /></MemoryRouter></StrictMode>)
    await search(user)
    expect(await screen.findByRole("img", { name: "a" })).toBeInTheDocument()
    expect(await screen.findByText("Annoyance")).toBeInTheDocument()
  })

  describe("final fix wave", () => {
    const facetsJoy = [{ name: "emotion", buckets: [{ value: "joy", count: 1 }] }]

    it("keeps Show more focusable (aria-disabled, not disabled) while loading and moves focus to the first new card", async () => {
      const user = userEvent.setup()
      let resolveMore: (value: unknown) => void = () => {}
      const search_ = vi.fn()
        .mockResolvedValueOnce(makeResponse({ items: [makeResult("a"), makeResult("b")], hasNext: true, nextCursor: "c1" }))
        .mockImplementationOnce(() => new Promise((resolve) => { resolveMore = resolve }))
      renderPage(makeMockApi({ searchMemeFromText: search_ }))
      await search(user)
      const more = await screen.findByRole("button", { name: "Show more" })
      more.focus()
      await user.keyboard("{Enter}")

      const loading = await screen.findByRole("button", { name: "Loading…" })
      expect(loading).not.toBeDisabled()
      expect(loading).toHaveAttribute("aria-disabled", "true")
      expect(loading).toHaveFocus()

      await act(async () => { resolveMore(makeResponse({ items: [makeResult("c"), makeResult("d")] })) })

      const newCard = screen.getByRole("img", { name: "c" }).closest("div.flex.flex-col") as HTMLElement
      expect(newCard).not.toBeNull()
      expect(newCard).toContainElement(document.activeElement as HTMLElement)
      expect(document.activeElement).not.toBe(document.body)
      expect(screen.queryByRole("button", { name: "Show more" })).not.toBeInTheDocument()
    })

    it("shows the unavailable text, no reading and no Try again, when the endpoint answers 404", async () => {
      const user = userEvent.setup()
      renderPage(makeMockApi({ searchMemeFromText: vi.fn().mockRejectedValue(new MessageSearchFailedError(404)) }))
      await search(user)
      expect(await screen.findByText("Message search isn't available in this environment.")).toBeInTheDocument()
      expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument()
      expect(screen.queryByRole("alert")).not.toBeInTheDocument()
      expect(screen.queryByRole("heading", { name: "How I read your message" })).not.toBeInTheDocument()
    })

    it("keeps the results area mounted when a filter is removed from the empty state, and focus is not lost", async () => {
      const user = userEvent.setup()
      let resolveRefine: (value: unknown) => void = () => {}
      const search_ = vi.fn()
        .mockResolvedValueOnce(makeResponse({ items: [makeResult("a")], facets: facetsJoy }))
        .mockResolvedValueOnce(makeResponse({ items: [] }))
        .mockImplementationOnce(() => new Promise((resolve) => { resolveRefine = resolve }))
      renderPage(makeMockApi({ searchMemeFromText: search_ }))
      await search(user)
      await user.click(await screen.findByLabelText("Joy (1)"))
      expect(await screen.findByText("No memes fit")).toBeInTheDocument()

      await user.click(screen.getByRole("button", { name: "Remove filter emotion: Joy" }))

      const results = screen.getByRole("region", { name: "Results" })
      expect(results).toHaveFocus()
      expect(screen.getByRole("region", { name: "Filters" })).toBeInTheDocument()
      expect(document.activeElement).not.toBe(document.body)
      await act(async () => { resolveRefine(makeResponse({ items: [makeResult("a")], facets: facetsJoy })) })
      expect(await screen.findByRole("img", { name: "a" })).toBeInTheDocument()
    })

    it("a failed refine from an empty list keeps the facets and the filters", async () => {
      const user = userEvent.setup()
      const search_ = vi.fn()
        .mockResolvedValueOnce(makeResponse({ items: [makeResult("a")], facets: facetsJoy }))
        .mockResolvedValueOnce(makeResponse({ items: [] }))
        .mockRejectedValueOnce(new MessageSearchFailedError(500))
      renderPage(makeMockApi({ searchMemeFromText: search_ }))
      await search(user)
      await user.click(await screen.findByLabelText("Joy (1)"))
      await screen.findByText("No memes fit")

      await user.click(screen.getByRole("button", { name: "Remove filter emotion: Joy" }))

      expect(await screen.findByRole("alert")).toBeInTheDocument()
      expect(screen.getByRole("region", { name: "Filters" })).toBeInTheDocument()
      expect(screen.getByRole("region", { name: "Results" })).toBeInTheDocument()
    })

    it("a failed first search shows only the banner", async () => {
      const user = userEvent.setup()
      renderPage(makeMockApi({ searchMemeFromText: vi.fn().mockRejectedValue(new MessageSearchFailedError(500)) }))
      await search(user)
      expect(await screen.findByRole("alert")).toBeInTheDocument()
      expect(screen.queryByRole("region", { name: "Results" })).not.toBeInTheDocument()
    })

    it("shows skeleton cards while a restored session waits for its first response", async () => {
      window.sessionStorage.setItem("reply.v1", JSON.stringify({
        text: "hello", submitted: "hello", originalProfile: makeProfile(), editedProfile: null, filters: {},
      }))
      const { container } = render(
        <MemoryRouter><ReplyPage memesApi={makeMockApi({ searchMemeFromText: vi.fn().mockImplementation(() => new Promise(() => {})) })} /></MemoryRouter>,
      )
      await waitFor(() => expect(container.querySelectorAll(".animate-pulse")).toHaveLength(3))
      expect(screen.queryByRole("region", { name: "Results" })).not.toBeInTheDocument()
    })

    it("dims the results and marks them busy while refining", async () => {
      const user = userEvent.setup()
      const search_ = vi.fn()
        .mockResolvedValueOnce(makeResponse({ items: [makeResult("a")], facets: facetsJoy }))
        .mockImplementationOnce(() => new Promise(() => {}))
      renderPage(makeMockApi({ searchMemeFromText: search_ }))
      await search(user)
      await user.click(await screen.findByLabelText("Joy (1)"))
      const area = screen.getByRole("region", { name: "Results" }).parentElement as HTMLElement
      expect(area).toHaveAttribute("aria-busy", "true")
      expect(area.className).toContain("opacity-60")
    })

    it("Reset to original reading moves focus to the Situation input", async () => {
      const user = userEvent.setup()
      renderPage(makeMockApi({ searchMemeFromText: vi.fn().mockResolvedValue(makeResponse({ items: [makeResult("a")] })) }))
      await search(user)
      await user.click(await screen.findByRole("button", { name: "Remove emotion: Annoyance" }))
      const reset = await screen.findByRole("button", { name: "Reset to original reading" })

      await user.click(reset)

      expect(screen.queryByRole("button", { name: "Reset to original reading" })).not.toBeInTheDocument()
      expect(screen.getByLabelText("Situation")).toHaveFocus()
    })

    it("Clear filters moves focus to the results section", async () => {
      const user = userEvent.setup()
      renderPage(makeMockApi({ searchMemeFromText: vi.fn().mockResolvedValue(makeResponse({ items: [makeResult("a")], facets: facetsJoy })) }))
      await search(user)
      await user.click(await screen.findByLabelText("Joy (1)"))
      await waitFor(() => expect(screen.getByRole("button", { name: "Clear filters" })).toBeInTheDocument())

      await user.click(screen.getByRole("button", { name: "Clear filters" }))

      expect(screen.getByRole("region", { name: "Results" })).toHaveFocus()
    })

    it("removing the last filter chip moves focus to the results section", async () => {
      const user = userEvent.setup()
      renderPage(makeMockApi({ searchMemeFromText: vi.fn().mockResolvedValue(makeResponse({ items: [makeResult("a")], facets: facetsJoy })) }))
      await search(user)
      await user.click(await screen.findByLabelText("Joy (1)"))
      await user.click(await screen.findByRole("button", { name: "Remove filter emotion: Joy" }))
      expect(screen.getByRole("region", { name: "Results" })).toHaveFocus()
    })

    it("announces '1 meme shown, more available' while more pages exist", async () => {
      const user = userEvent.setup()
      renderPage(makeMockApi({
        searchMemeFromText: vi.fn().mockResolvedValue(makeResponse({ items: [makeResult("a")], hasNext: true, nextCursor: "c1" })),
      }))
      await search(user)
      expect(await screen.findByText("1 meme shown, more available")).toBeInTheDocument()
    })

    it("does not steal focus when Show more was ignored during an edit debounce and the refine then replaces the list", async () => {
      const user = userEvent.setup()
      const search_ = vi.fn()
        .mockResolvedValueOnce(makeResponse({ items: [makeResult("a")], hasNext: true, nextCursor: "c1" }))
        .mockResolvedValueOnce(makeResponse({ items: [makeResult("a"), makeResult("b")], hasNext: true, nextCursor: "c2" }))
      const api = renderPage(makeMockApi({ searchMemeFromText: search_ }))
      await search(user)
      await user.click(await screen.findByRole("button", { name: "Remove emotion: Annoyance" }))
      const more = screen.getByRole("button", { name: "Show more" })
      await user.click(more)
      expect(calls(api)).toHaveLength(1)

      await waitFor(() => expect(screen.getAllByRole("img", { name: /^[ab]$/ })).toHaveLength(2), { timeout: 2000 })

      expect(calls(api)).toHaveLength(2)
      for (const img of screen.getAllByRole("img", { name: /^[ab]$/ })) {
        expect(img.closest("div.flex.flex-col")).not.toContainElement(document.activeElement as HTMLElement)
      }
    })

    it("moves focus to Try again when a keyboard-activated Show more fails", async () => {
      const user = userEvent.setup()
      const search_ = vi.fn()
        .mockResolvedValueOnce(makeResponse({ items: [makeResult("a")], hasNext: true, nextCursor: "c1" }))
        .mockRejectedValueOnce(new MessageSearchFailedError(500))
      renderPage(makeMockApi({ searchMemeFromText: search_ }))
      await search(user)
      ;(await screen.findByRole("button", { name: "Show more" })).focus()
      await user.keyboard("{Enter}")

      expect(await screen.findByRole("alert")).toBeInTheDocument()
      expect(screen.getByRole("button", { name: "Try again" })).toHaveFocus()
    })

    it("moves focus to the Results section when the last page adds no new cards", async () => {
      const user = userEvent.setup()
      const search_ = vi.fn()
        .mockResolvedValueOnce(makeResponse({ items: [makeResult("a")], hasNext: true, nextCursor: "c1" }))
        .mockResolvedValueOnce(makeResponse({ items: [makeResult("a")] }))
      renderPage(makeMockApi({ searchMemeFromText: search_ }))
      await search(user)
      ;(await screen.findByRole("button", { name: "Show more" })).focus()
      await user.keyboard("{Enter}")

      await waitFor(() => expect(screen.queryByRole("button", { name: "Show more" })).not.toBeInTheDocument())
      expect(screen.getByRole("region", { name: "Results" })).toHaveFocus()
    })

    it("retries the vocabulary after a search response when its first load failed", async () => {
      const user = userEvent.setup()
      let down = true
      const getTaxonomy = vi.fn().mockImplementation(() => (down ? Promise.reject(new Error("down")) : Promise.resolve(TEST_TAXONOMY)))
      render(<StrictMode><MemoryRouter><ReplyPage memesApi={makeMockApi({ getTaxonomy, searchMemeFromText: vi.fn().mockImplementation(async () => { down = false; return makeResponse({ items: [makeResult("a")] }) }) })} /></MemoryRouter></StrictMode>)
      await search(user)
      expect(await screen.findByText("Annoyance")).toBeInTheDocument()
    })
  })
})

const HASH = "a".repeat(64)

function ratedResponse(extra = {}) {
  return makeResponse({ items: [makeResult("a"), makeResult("b")], ...extra })
}

describe("ReplyPage ratings", () => {
  it("rates a result with the text that was searched, its rank and the parsed reading", async () => {
    const user = userEvent.setup()
    const api = renderPage(makeMockApi({ searchMemeFromText: vi.fn().mockResolvedValue(ratedResponse()) }))
    await search(user, "my manager moved the meeting")
    await screen.findAllByRole("group", { name: "Rate this result" })

    const groups = screen.getAllByRole("group", { name: "Rate this result" })
    await user.click(within(groups[1]).getByRole("button", { name: "Good fit" }))

    await waitFor(() => expect(api.putRating).toHaveBeenCalledTimes(1))
    expect(api.putRating).toHaveBeenCalledWith(expect.objectContaining({
      text: "my manager moved the meeting", imageId: "b", grade: 2, rank: 2, reading: "parsed",
      parserVersion: "p3-test", taxonomyVersion: 2, degraded: false, fallback: "none", language: "en",
    }))
    expect(within(groups[1]).getByRole("button", { name: "Good fit" })).toHaveAttribute("aria-pressed", "true")
  })

  it("uses the submitted text, not the box's current text, and marks an edited reading", async () => {
    const user = userEvent.setup()
    const api = renderPage(makeMockApi({ searchMemeFromText: vi.fn().mockResolvedValue(ratedResponse()) }))
    await search(user, "first message")
    await screen.findAllByRole("group", { name: "Rate this result" })
    await user.type(screen.getByLabelText("Message to reply to"), " and more typed afterwards")
    await user.click(screen.getByRole("button", { name: "Remove emotion: Annoyance" }))
    await waitFor(() => expect(screen.getByText("Edited")).toBeInTheDocument())
    // the edited reading counts once the refine that used it has returned
    await waitFor(() => expect(api.searchMemeFromText).toHaveBeenCalledTimes(2), { timeout: 2000 })
    await waitFor(() => expect(screen.getByText("2 memes found")).toBeInTheDocument())

    await user.click(within(screen.getAllByRole("group", { name: "Rate this result" })[0]).getByRole("button", { name: "Acceptable" }))

    await waitFor(() => expect(api.putRating).toHaveBeenCalled())
    expect(api.putRating).toHaveBeenCalledWith(expect.objectContaining({ text: "first message", reading: "edited", grade: 1 }))
  })

  it("shows grades loaded for the message and keeps them after a refine", async () => {
    const user = userEvent.setup()
    const api = renderPage(makeMockApi({
      searchMemeFromText: vi.fn().mockResolvedValue(ratedResponse()),
      getRatings: vi.fn().mockResolvedValue({ queryHash: HASH, ratings: [{ imageId: "a", grade: 0 }] }),
    }))
    await search(user)
    const first = (await screen.findAllByRole("group", { name: "Rate this result" }))[0]
    await waitFor(() => expect(within(first).getByRole("button", { name: "Not a fit" })).toHaveAttribute("aria-pressed", "true"))
    expect(api.getRatings).toHaveBeenCalledWith(HASH)

    await user.click(screen.getByRole("button", { name: "Remove emotion: Annoyance" }))
    await waitFor(() => expect(calls(api).length).toBeGreaterThanOrEqual(2), { timeout: 2000 })

    const again = (await screen.findAllByRole("group", { name: "Rate this result" }))[0]
    expect(within(again).getByRole("button", { name: "Not a fit" })).toHaveAttribute("aria-pressed", "true")
  })

  it("clicking the active grade clears the rating", async () => {
    const user = userEvent.setup()
    const api = renderPage(makeMockApi({
      searchMemeFromText: vi.fn().mockResolvedValue(ratedResponse()),
      getRatings: vi.fn().mockResolvedValue({ queryHash: HASH, ratings: [{ imageId: "a", grade: 2 }] }),
    }))
    await search(user)
    const first = (await screen.findAllByRole("group", { name: "Rate this result" }))[0]
    await waitFor(() => expect(within(first).getByRole("button", { name: "Good fit" })).toHaveAttribute("aria-pressed", "true"))

    await user.click(within(first).getByRole("button", { name: "Good fit" }))

    await waitFor(() => expect(api.deleteRating).toHaveBeenCalledWith(HASH, "a"))
  })

  it("shows an inline error and rolls back when saving fails", async () => {
    const user = userEvent.setup()
    renderPage(makeMockApi({
      searchMemeFromText: vi.fn().mockResolvedValue(ratedResponse()),
      putRating: vi.fn().mockRejectedValue(new Error("down")),
    }))
    await search(user)
    const first = (await screen.findAllByRole("group", { name: "Rate this result" }))[0]
    await user.click(within(first).getByRole("button", { name: "Good fit" }))

    expect(await within(first).findByRole("alert")).toHaveTextContent("Couldn't save the rating")
    expect(within(first).getByRole("button", { name: "Good fit" })).toHaveAttribute("aria-pressed", "false")
  })

  it("offers to forget the text only once a message has ratings, and forgets it after confirmation", async () => {
    const user = userEvent.setup()
    const api = renderPage(makeMockApi({
      searchMemeFromText: vi.fn().mockResolvedValue(ratedResponse()),
      getRatings: vi.fn().mockResolvedValue({ queryHash: HASH, ratings: [] }),
    }))
    await search(user)
    await screen.findAllByRole("group", { name: "Rate this result" })
    expect(screen.queryByRole("button", { name: "Forget this message's text" })).not.toBeInTheDocument()
    // the space is reserved from the start, so the first rating does not shift the cards
    const slot = screen.getByTestId("forget-slot")
    expect(slot).toBeEmptyDOMElement()
    expect(slot.className).toContain("min-h-11")

    await user.click(within(screen.getAllByRole("group", { name: "Rate this result" })[0]).getByRole("button", { name: "Acceptable" }))
    await user.click(await screen.findByRole("button", { name: "Forget this message's text" }))
    expect(screen.getByTestId("forget-slot")).toBe(slot)
    await user.click(screen.getByRole("button", { name: "Confirm" }))

    await waitFor(() => expect(api.forgetRatingText).toHaveBeenCalledWith(HASH))
  })

  it("shows no rating bar when the response carries no query hash or the page is unavailable", async () => {
    const user = userEvent.setup()
    const response = ratedResponse()
    delete (response.meta as { queryHash?: string }).queryHash
    renderPage(makeMockApi({ searchMemeFromText: vi.fn().mockResolvedValue(response) }))
    await search(user)
    await screen.findAllByRole("img", { name: /^[ab]$/ })
    expect(screen.queryByRole("group", { name: "Rate this result" })).not.toBeInTheDocument()
  })
})

describe("ReplyPage ratings (fix wave)", () => {
  it("rates with the reading that produced the list: parsed until the edited refine returns", async () => {
    const user = userEvent.setup()
    const api = renderPage(makeMockApi({ searchMemeFromText: vi.fn().mockResolvedValue(ratedResponse()) }))
    await search(user)
    await screen.findAllByRole("group", { name: "Rate this result" })
    await user.click(screen.getByRole("button", { name: "Remove emotion: Annoyance" }))
    await waitFor(() => expect(screen.getByText("Edited")).toBeInTheDocument())
    expect(calls(api)).toHaveLength(1)

    await user.click(within(screen.getAllByRole("group", { name: "Rate this result" })[0]).getByRole("button", { name: "Acceptable" }))
    await waitFor(() => expect(api.putRating).toHaveBeenCalledTimes(1))
    expect(api.putRating).toHaveBeenLastCalledWith(expect.objectContaining({ reading: "parsed" }))

    await waitFor(() => expect(calls(api)).toHaveLength(2), { timeout: 2000 })
    await waitFor(() => expect(screen.getByText("2 memes found")).toBeInTheDocument())
    await user.click(within(screen.getAllByRole("group", { name: "Rate this result" })[1]).getByRole("button", { name: "Good fit" }))
    await waitFor(() => expect(api.putRating).toHaveBeenCalledTimes(2))
    expect(api.putRating).toHaveBeenLastCalledWith(expect.objectContaining({ reading: "edited" }))
  })

  it("after reset to the original reading, ratings stay 'edited' until the refine returns", async () => {
    const user = userEvent.setup()
    let resolveRefine!: (value: ReturnType<typeof ratedResponse>) => void
    const pending = new Promise<ReturnType<typeof ratedResponse>>((res) => { resolveRefine = res })
    const search_ = vi.fn()
      .mockResolvedValueOnce(ratedResponse())
      .mockResolvedValueOnce(ratedResponse())
      .mockReturnValueOnce(pending)
    const api = renderPage(makeMockApi({ searchMemeFromText: search_ }))
    await search(user)
    await screen.findAllByRole("group", { name: "Rate this result" })
    await user.click(screen.getByRole("button", { name: "Remove emotion: Annoyance" }))
    await waitFor(() => expect(calls(api)).toHaveLength(2), { timeout: 2000 })
    await waitFor(() => expect(screen.getByText("2 memes found")).toBeInTheDocument())

    await user.click(screen.getByRole("button", { name: /reset to original/i }))
    await waitFor(() => expect(calls(api)).toHaveLength(3))
    await user.click(within(screen.getAllByRole("group", { name: "Rate this result" })[0]).getByRole("button", { name: "Acceptable" }))
    await waitFor(() => expect(api.putRating).toHaveBeenCalledTimes(1))
    expect(api.putRating).toHaveBeenLastCalledWith(expect.objectContaining({ reading: "edited" }))

    await act(async () => { resolveRefine(ratedResponse()) })
    await waitFor(() => expect(screen.getByText("2 memes found")).toBeInTheDocument())
    await user.click(within(screen.getAllByRole("group", { name: "Rate this result" })[1]).getByRole("button", { name: "Good fit" }))
    await waitFor(() => expect(api.putRating).toHaveBeenCalledTimes(2))
    expect(api.putRating).toHaveBeenLastCalledWith(expect.objectContaining({ reading: "parsed" }))
  })

  it("clears the 'Text forgotten' notice when a later rating stores the text again", async () => {
    const user = userEvent.setup()
    renderPage(makeMockApi({ searchMemeFromText: vi.fn().mockResolvedValue(ratedResponse()) }))
    await search(user)
    const groups = await screen.findAllByRole("group", { name: "Rate this result" })
    await user.click(within(groups[0]).getByRole("button", { name: "Acceptable" }))
    await user.click(await screen.findByRole("button", { name: "Forget this message's text" }))
    await user.click(screen.getByRole("button", { name: "Confirm" }))
    expect(await screen.findByText("Text forgotten", { selector: '[aria-hidden="true"]' })).toBeInTheDocument()

    await user.click(within(screen.getAllByRole("group", { name: "Rate this result" })[1]).getByRole("button", { name: "Good fit" }))

    await waitFor(() => expect(screen.queryByText("Text forgotten", { selector: '[aria-hidden="true"]' })).not.toBeInTheDocument())
    expect(screen.getByRole("button", { name: "Forget this message's text" })).toBeInTheDocument()
  })

  it("continues the rank across Show more", async () => {
    const user = userEvent.setup()
    const search_ = vi.fn()
      .mockResolvedValueOnce(makeResponse({ items: [makeResult("a"), makeResult("b")], hasNext: true, nextCursor: "c1" }))
      .mockResolvedValueOnce(makeResponse({ items: [makeResult("c")] }))
    const api = renderPage(makeMockApi({ searchMemeFromText: search_ }))
    await search(user)
    await user.click(await screen.findByRole("button", { name: "Show more" }))
    await waitFor(() => expect(screen.getAllByRole("group", { name: "Rate this result" })).toHaveLength(3))

    await user.click(within(screen.getAllByRole("group", { name: "Rate this result" })[2]).getByRole("button", { name: "Good fit" }))

    await waitFor(() => expect(api.putRating).toHaveBeenCalled())
    expect(api.putRating).toHaveBeenCalledWith(expect.objectContaining({ imageId: "c", rank: 3 }))
  })

  it("shows the ratings again after re-searching the same text", async () => {
    const user = userEvent.setup()
    const api = renderPage(makeMockApi({
      searchMemeFromText: vi.fn().mockResolvedValue(ratedResponse()),
      getRatings: vi.fn().mockResolvedValue({ queryHash: HASH, ratings: [{ imageId: "a", grade: 1 }] }),
    }))
    await search(user)
    const first = (await screen.findAllByRole("group", { name: "Rate this result" }))[0]
    await waitFor(() => expect(within(first).getByRole("button", { name: "Acceptable" })).toHaveAttribute("aria-pressed", "true"))

    await user.click(screen.getByRole("button", { name: "Find a meme" }))
    await waitFor(() => expect(calls(api)).toHaveLength(2))
    const again = (await screen.findAllByRole("group", { name: "Rate this result" }))[0]
    await waitFor(() => expect(within(again).getByRole("button", { name: "Acceptable" })).toHaveAttribute("aria-pressed", "true"))
    expect(api.getRatings).toHaveBeenCalledWith(HASH)
  })

  it("offers 'I didn't find it' for a fresh search and reports its id", async () => {
    const user = userEvent.setup()
    const api = renderPage(makeMockApi({
      searchMemeFromText: vi.fn().mockResolvedValue(makeResponse({ items: [makeResult("a")], searchId: "s1" })),
    }))
    await search(user)
    await user.click(await screen.findByRole("button", { name: "I didn't find it" }))
    expect(api.reportNotFound).toHaveBeenCalledWith("s1")
  })

  it("has no 'I didn't find it' button when the response carries no search id", async () => {
    const user = userEvent.setup()
    renderPage(makeMockApi({
      searchMemeFromText: vi.fn().mockResolvedValue(makeResponse({ items: [makeResult("a")] })),
    }))
    await search(user)
    await screen.findByText("1 meme found")
    expect(screen.queryByRole("button", { name: "I didn't find it" })).not.toBeInTheDocument()
  })
})
