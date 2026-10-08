import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { StrictMode } from "react"
import { MemoryRouter } from "react-router-dom"
import ReplyPage from "./ReplyPage"
import { makeMockApi } from "../test/mockApi"
import { makeProfile, makeResponse, makeResult } from "../reply/fixtures"
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
})
