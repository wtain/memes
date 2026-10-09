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

  function runsOf(profiles: unknown, embeddings: unknown) {
    return makeCoverage({ lastRuns: { profiles, embeddings } as never })
  }

  it("reads the nested coverage stats of a completed profiles run and the embeddings run", async () => {
    setup({
      getProfileCoverage: vi.fn().mockResolvedValue(runsOf(
        {
          runId: "r", status: "completed", startedAt: "2026-10-09T09:00:00", completedAt: "2026-10-09T10:00:00",
          stats: { saved: 3, images_remaining: 12, coverage: { stale: 7, needs_review: 4, failed: 2, active: 100 } },
        },
        {
          runId: "e", status: "completed", startedAt: "2026-10-09T10:00:00", completedAt: "2026-10-09T10:30:00",
          stats: { embedded: 40, images_remaining: 5 },
        },
      )),
    })
    const block = (await screen.findByText("Profiles run")).closest("section") as HTMLElement
    expect(within(block).getByText("Stale").nextSibling).toHaveTextContent("7")
    expect(within(block).getByText("Needs review").nextSibling).toHaveTextContent("4")
    expect(within(block).getByText("Failed").nextSibling).toHaveTextContent("2")
    expect(within(block).getByText("Still to profile").nextSibling).toHaveTextContent("12")
    expect(block).toHaveTextContent("Completed:")
    const embeddings = screen.getByText("Embeddings run").closest("section") as HTMLElement
    expect(within(embeddings).getByText("Embedded").nextSibling).toHaveTextContent("40")
    expect(within(embeddings).getByText("Still to embed").nextSibling).toHaveTextContent("5")
    expect(screen.getByText("Active memes").nextSibling).toHaveTextContent("100")
  })

  it("shows dashes for missing or non-numeric stats of a completed run", async () => {
    setup({
      getProfileCoverage: vi.fn().mockResolvedValue(runsOf(
        { runId: "r", status: "completed", startedAt: "2026-10-09T09:00:00", completedAt: "2026-10-09T10:00:00", stats: { coverage: { stale: "x" } } },
        null,
      )),
    })
    const block = (await screen.findByText("Profiles run")).closest("section") as HTMLElement
    expect(within(block).getByText("Stale").nextSibling).toHaveTextContent("-")
    expect(within(block).getByText("Failed").nextSibling).toHaveTextContent("-")
  })

  it("shows a started run as in progress without numbers", async () => {
    setup({
      getProfileCoverage: vi.fn().mockResolvedValue(runsOf(
        { runId: "r", status: "started", startedAt: "2026-10-09T09:00:00", completedAt: null, stats: null },
        null,
      )),
    })
    const block = (await screen.findByText("Profiles run")).closest("section") as HTMLElement
    expect(block).toHaveTextContent("A run is in progress: numbers appear when it finishes")
    expect(block).toHaveTextContent("running since")
    expect(within(block).queryByText("Stale")).not.toBeInTheDocument()
  })

  it("says No run yet for each job without a run", async () => {
    setup({ getProfileCoverage: vi.fn().mockResolvedValue(runsOf(null, null)) })
    await screen.findByText("Profiles run")
    expect(screen.getAllByText("No run yet")).toHaveLength(2)
    expect(screen.getByText("Embeddings run")).toBeInTheDocument()
  })

  it("retries a failed first list load of the selected category", async () => {
    const user = userEvent.setup()
    const api = setup({
      getProfileCoverageImages: vi.fn().mockRejectedValueOnce(new Error("x")).mockResolvedValue(makeCoverageImages(["a"])),
    })
    const alert = await screen.findByText("Couldn't load the memes")
    await user.click(within(alert.parentElement as HTMLElement).getByRole("button", { name: "Try again" }))
    expect(await screen.findByRole("link", { name: "Meme a" })).toBeInTheDocument()
    expect(api.getProfileCoverageImages).toHaveBeenCalledTimes(2)
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
