import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import ProfileCoveragePage from "./ProfileCoveragePage"
import { makeCoverage, makeCoverageImages } from "../curator/fixtures"
import { makeMockApi } from "../test/mockApi"

function setup(overrides: Parameters<typeof makeMockApi>[0] = {}) {
  const api = makeMockApi(overrides)
  render(
    <MemoryRouter>
      <ProfileCoveragePage memesApi={api} />
    </MemoryRouter>,
  )
  return api
}

describe("ProfileCoveragePage", () => {
  it("renders the heading and one toggle card per category with its count", async () => {
    setup()
    expect(screen.getByRole("heading", { name: "Profile coverage" })).toBeInTheDocument()
    const first = await screen.findByRole("button", { name: /Without profile/ })
    expect(first).toHaveTextContent("25")
    expect(first).toHaveAttribute("aria-pressed", "true")
    expect(screen.getByRole("button", { name: /Failed/ })).toHaveAttribute("aria-pressed", "false")
    expect(screen.getByRole("button", { name: /Failed/ })).toHaveTextContent("2")
    expect(screen.getAllByRole("button", { pressed: false })).toHaveLength(5)
    expect(first).toHaveClass("min-h-11")
  })

  it("selects a card, announces it and shows linked thumbnails", async () => {
    const user = userEvent.setup()
    const api = setup({
      getProfileCoverageImages: vi.fn()
        .mockResolvedValueOnce(makeCoverageImages(["a"]))
        .mockResolvedValueOnce(makeCoverageImages(["abcdef123456", "b"])),
    })
    await screen.findByRole("button", { name: /Failed/ })
    expect(screen.getByRole("status")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: /Failed/ }))
    expect(screen.getByRole("button", { name: /Failed/ })).toHaveAttribute("aria-pressed", "true")
    expect(screen.getByRole("status")).toHaveTextContent("Failed: 2 memes")
    const link = await screen.findByRole("link", { name: "Meme abcdef12" })
    expect(link).toHaveAttribute("href", "/memes/abcdef123456")
    const img = within(link).getByRole("img")
    expect(img).toHaveAttribute("loading", "lazy")
    expect(img).toHaveAttribute("src", "http://example.com/api/images/abcdef123456")
    expect(api.getProfileCoverageImages).toHaveBeenLastCalledWith("failed", undefined)
  })

  it("shows Show more only when more pages exist and appends", async () => {
    const user = userEvent.setup()
    setup({
      getProfileCoverageImages: vi.fn()
        .mockResolvedValueOnce(makeCoverageImages(["a"], "c1"))
        .mockResolvedValueOnce(makeCoverageImages(["b"], null)),
    })
    await screen.findByRole("link", { name: "Meme a" })
    await user.click(screen.getByRole("button", { name: "Show more" }))
    await screen.findByRole("link", { name: "Meme b" })
    expect(screen.getByRole("link", { name: "Meme a" })).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Show more" })).not.toBeInTheDocument()
  })

  it("says so for an empty category", async () => {
    setup({ getProfileCoverageImages: vi.fn().mockResolvedValue(makeCoverageImages([])) })
    expect(await screen.findByText("Nothing in this category")).toBeInTheDocument()
  })

  it("shows the last run numbers with its time, and No run yet for null", async () => {
    const api = makeMockApi({
      getProfileCoverage: vi.fn().mockResolvedValue(makeCoverage({
        lastRuns: {
          profiles: {
            runId: "r", status: "done", startedAt: "2026-10-09T09:00:00", completedAt: "2026-10-09T10:00:00",
            stats: { stale: 7, needs_review: 4 },
          },
          embeddings: null,
        },
      })),
    })
    render(<MemoryRouter><ProfileCoveragePage memesApi={api} /></MemoryRouter>)
    const block = (await screen.findByText("As of the last run")).closest("section") as HTMLElement
    expect(within(block).getByText("Stale").nextSibling).toHaveTextContent("7")
    expect(within(block).getByText("Needs review").nextSibling).toHaveTextContent("4")
    expect(within(block).getByText("Failed").nextSibling).toHaveTextContent("-")
    expect(block).toHaveTextContent("Completed:")
    expect(screen.getByText("Active memes").nextSibling).toHaveTextContent("100")
    expect(screen.getByText("Profiled").nextSibling).toHaveTextContent("75")
  })

  it("says No run yet when there is no profile run", async () => {
    setup({
      getProfileCoverage: vi.fn().mockResolvedValue(makeCoverage({ lastRuns: { profiles: null, embeddings: null } })),
    })
    expect(await screen.findByText("No run yet")).toBeInTheDocument()
  })

  it("shows an alert with Try again when the coverage fails", async () => {
    const user = userEvent.setup()
    const api = setup({
      getProfileCoverage: vi.fn().mockRejectedValueOnce(new Error("x")).mockResolvedValue(makeCoverage()),
    })
    const alert = await screen.findByRole("alert")
    expect(alert).toHaveTextContent("Couldn't load the coverage")
    await user.click(within(alert).getByRole("button", { name: "Try again" }))
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument())
    expect(await screen.findByRole("button", { name: /Without profile/ })).toBeInTheDocument()
    expect(api.getProfileCoverage).toHaveBeenCalledTimes(2)
  })

  it("marks human-verified thumbnails", async () => {
    setup({
      getProfileCoverageImages: vi.fn().mockResolvedValue({
        items: [{ id: "v-1", imageUrl: "/api/images/v-1", humanVerified: true }],
        nextCursor: null,
        total: 1,
      }),
    })
    expect(await screen.findByText("Verified")).toBeInTheDocument()
  })
})
