import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import ComicsCandidatesPage from "./ComicsCandidatesPage"
import { ComicsRequestError } from "../api/errors"
import { makeMockApi } from "../test/mockApi"
import type { ComicsCandidate } from "../types/generated/all"

function cand(id: string, imageIds: string[] = [`${id}1`, `${id}2`, `${id}3`]): ComicsCandidate {
  return {
    id, maxDistance: 0.1, maxGapSeconds: 3,
    images: imageIds.map((i) => ({ id: i, filename: `file-${i}.jpg`, width: 800, height: 600 })),
  }
}

function setup(overrides: Parameters<typeof makeMockApi>[0] = {}) {
  const api = makeMockApi({
    listComicsCandidates: vi.fn().mockResolvedValue({ items: [cand("a"), cand("b")], hasMore: false }),
    acceptComicsCandidate: vi.fn().mockResolvedValue({ id: "new1", name: "n", state: "draft", panels: [], builtImageId: null, builtAt: null }),
    ...overrides,
  })
  render(
    <MemoryRouter initialEntries={["/admin/comics/candidates"]}>
      <Routes>
        <Route path="/admin/comics/candidates" element={<ComicsCandidatesPage memesApi={api} />} />
        <Route path="/admin/comics/:id" element={<p>Editor of the new comics</p>} />
      </Routes>
    </MemoryRouter>,
  )
  return api
}

const rowOf = (id: string) => screen.getByRole("listitem", { name: `Suggestion ${id}` })

describe("ComicsCandidatesPage", () => {
  it("loads and shows rows with thumbnails, file names and size", async () => {
    setup()
    expect(await screen.findByAltText("file-a1.jpg")).toHaveAttribute("src", "http://example.com/api/images/a1")
    expect(within(rowOf("b")).getAllByRole("img")).toHaveLength(3)
    expect(within(rowOf("a")).getAllByText("800x600")).toHaveLength(3)
    expect(screen.getAllByRole("button", { name: "Make comics" })).toHaveLength(2)
  })

  it("makes comics with the enabled subset and opens the editor", async () => {
    const api = setup()
    await screen.findByAltText("file-a1.jpg")
    await userEvent.click(within(rowOf("a")).getByRole("button", { name: "Exclude file-a2.jpg" }))
    await userEvent.click(within(rowOf("a")).getByRole("button", { name: "Make comics" }))
    expect(api.acceptComicsCandidate).toHaveBeenCalledWith("a", ["a1", "a3"])
    expect(await screen.findByText("Editor of the new comics")).toBeInTheDocument()
  })

  it("sends every id when nothing is switched off, and toggling back restores one", async () => {
    const api = setup()
    await screen.findByAltText("file-a1.jpg")
    await userEvent.click(within(rowOf("a")).getByRole("button", { name: "Exclude file-a2.jpg" }))
    await userEvent.click(within(rowOf("a")).getByRole("button", { name: "Include file-a2.jpg" }))
    await userEvent.click(within(rowOf("a")).getByRole("button", { name: "Make comics" }))
    expect(api.acceptComicsCandidate).toHaveBeenCalledWith("a", ["a1", "a2", "a3"])
  })

  it("disables Make comics below two enabled images", async () => {
    const api = setup({ listComicsCandidates: vi.fn().mockResolvedValue({ items: [cand("a")], hasMore: false }) })
    await screen.findByAltText("file-a1.jpg")
    await userEvent.click(screen.getByRole("button", { name: "Exclude file-a1.jpg" }))
    expect(screen.getByRole("button", { name: "Make comics" })).toBeEnabled()
    await userEvent.click(screen.getByRole("button", { name: "Exclude file-a2.jpg" }))
    expect(screen.getByRole("button", { name: "Make comics" })).toBeDisabled()
    expect(api.acceptComicsCandidate).not.toHaveBeenCalled()
  })

  it("dismisses a row and calls the API", async () => {
    const api = setup()
    await screen.findByAltText("file-a1.jpg")
    await userEvent.click(within(rowOf("a")).getByRole("button", { name: "Dismiss" }))
    expect(api.dismissComicsCandidate).toHaveBeenCalledWith("a")
    await vi.waitFor(() => expect(screen.queryByAltText("file-a1.jpg")).not.toBeInTheDocument())
    expect(screen.getByAltText("file-b1.jpg")).toBeInTheDocument()
  })

  it("drops the row and keeps the reason in a page notice when the suggestion was already decided", async () => {
    setup({
      acceptComicsCandidate: vi.fn().mockRejectedValue(new ComicsRequestError(409, { code: "candidate_decided", state: "dismissed" })),
    })
    await screen.findByAltText("file-a1.jpg")
    await userEvent.click(within(rowOf("a")).getByRole("button", { name: "Make comics" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("Suggestion removed: This suggestion was already decided")
    expect(screen.queryByAltText("file-a1.jpg")).not.toBeInTheDocument()
    expect(screen.getByAltText("file-b1.jpg")).toBeInTheDocument()
  })

  it("says the suggestion no longer exists on not_found, and removes the row", async () => {
    setup({
      dismissComicsCandidate: vi.fn().mockRejectedValue(new ComicsRequestError(404, { code: "not_found" })),
    })
    await screen.findByAltText("file-a1.jpg")
    await userEvent.click(within(rowOf("a")).getByRole("button", { name: "Dismiss" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("Suggestion removed: This suggestion no longer exists")
    expect(screen.queryByAltText("file-a1.jpg")).not.toBeInTheDocument()
  })

  it("names the offending image by file name on image_in_comics and removes the row", async () => {
    setup({
      acceptComicsCandidate: vi.fn().mockRejectedValue(
        new ComicsRequestError(409, { code: "image_in_comics", comicsName: "Cats", imageId: "a2" })),
    })
    await screen.findByAltText("file-a1.jpg")
    await userEvent.click(within(rowOf("a")).getByRole("button", { name: "Make comics" }))
    expect(await screen.findByRole("alert")).toHaveTextContent(
      'Suggestion removed: The image "file-a2.jpg" is already a panel of the comics "Cats"')
    expect(screen.queryByAltText("file-a1.jpg")).not.toBeInTheDocument()
  })

  it("keeps the row with its message on a failure that leaves the suggestion listed", async () => {
    setup({
      acceptComicsCandidate: vi.fn().mockRejectedValue(new ComicsRequestError(422, { code: "subset_invalid", reason: "unknown_image" })),
    })
    await screen.findByAltText("file-a1.jpg")
    await userEvent.click(within(rowOf("a")).getByRole("button", { name: "Make comics" }))
    expect(await within(rowOf("a")).findByRole("alert")).toHaveTextContent("The images changed in the meantime")
    expect(screen.getByAltText("file-a1.jpg")).toBeInTheDocument()
  })

  it("shows the empty state naming the job", async () => {
    setup({ listComicsCandidates: vi.fn().mockResolvedValue({ items: [], hasMore: false }) })
    expect(await screen.findByText(/detect_comics_candidates/)).toBeInTheDocument()
  })

  it("shows an error with retry when loading fails", async () => {
    const listComicsCandidates = vi.fn().mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValue({ items: [cand("a")], hasMore: false })
    setup({ listComicsCandidates })
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load the suggestions")
    await userEvent.click(screen.getByRole("button", { name: "Retry" }))
    expect(await screen.findByAltText("file-a1.jpg")).toBeInTheDocument()
  })

  it("shows more by appending the next page at the offset of the loaded rows", async () => {
    const listComicsCandidates = vi.fn()
      .mockResolvedValueOnce({ items: [cand("a")], hasMore: true })
      .mockResolvedValueOnce({ items: [cand("b")], hasMore: false })
    setup({ listComicsCandidates })
    await userEvent.click(await screen.findByRole("button", { name: "Show more" }))
    expect(await screen.findByAltText("file-b1.jpg")).toBeInTheDocument()
    expect(listComicsCandidates).toHaveBeenLastCalledWith(1, expect.any(Number))
    expect(screen.getByAltText("file-a1.jpg")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Show more" })).not.toBeInTheDocument()
  })
})
