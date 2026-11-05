import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import ComicsEditorPage from "./ComicsEditorPage"
import { ComicsRequestError } from "../api/errors"
import { makeMockApi } from "../test/mockApi"
import type { ComicsDetail, ComicsPanel } from "../types/generated/all"

function panel(id: string, position: number): ComicsPanel {
  return { id, position, imageId: `img-${id}`, sourceFilename: `${id}.jpg` }
}
function detail(panelIds: string[], over: Partial<ComicsDetail> = {}): ComicsDetail {
  return { id: "c1", name: "My comics", state: "draft", builtImageId: null, builtAt: null,
    panels: panelIds.map((id, i) => panel(id, i)), ...over }
}
const free = (imageId: string) => ({ imageId, addable: true, reason: null, comicsId: null, comicsName: null })

function setup(overrides: Parameters<typeof makeMockApi>[0] = {}) {
  const api = makeMockApi({
    getComics: vi.fn().mockResolvedValue(detail(["p1", "p2"])),
    comicsPreviewUrl: vi.fn((id: string, w: number, v: string) => `http://x/api/comics/${id}/preview?w=${w}&v=${v}`),
    searchMemes: vi.fn().mockResolvedValue({ items: [{ id: "m1", imageUrl: "/i/m1", originalFileName: "m1.jpg" }], hasNext: false }),
    comicsAddability: vi.fn().mockResolvedValue({ items: [free("m1")] }),
    ...overrides,
  })
  render(
    <MemoryRouter initialEntries={["/admin/comics/c1"]}>
      <Routes><Route path="/admin/comics/:id" element={<ComicsEditorPage memesApi={api} />} /></Routes>
    </MemoryRouter>,
  )
  return api
}

async function search(text = "cat") {
  await userEvent.type(await screen.findByLabelText("Search memes"), text)
  await userEvent.click(screen.getByRole("button", { name: "Search" }))
}

describe("ComicsEditorPage draft", () => {
  it("shows the panel strip, numbered, and the disabled build button", async () => {
    setup()
    expect(await screen.findByText("p1.jpg")).toBeInTheDocument()
    expect(screen.getByText("p2.jpg")).toBeInTheDocument()
    const build = screen.getByRole("button", { name: "Build" })
    expect(build).toBeDisabled()
    expect(build).toHaveAttribute("title", "Build is not available yet")
  })

  it("searches with record false and checks addability once for the results", async () => {
    const api = setup()
    await search()
    expect(await screen.findByRole("button", { name: "Add m1.jpg" })).toBeEnabled()
    expect(api.searchMemes).toHaveBeenCalledWith(expect.objectContaining({ query: "cat", record: false }))
    expect(api.comicsAddability).toHaveBeenCalledTimes(1)
    expect(api.comicsAddability).toHaveBeenCalledWith(["m1"])
  })

  it("add grows the strip and re-fetches addability for the visible results", async () => {
    const added = detail(["p1", "p2", "p3"])
    const addability = vi.fn().mockResolvedValueOnce({ items: [free("m1")] })
      .mockResolvedValue({ items: [{ ...free("m1"), addable: false, reason: "in_comics", comicsId: "c1", comicsName: "My comics" }] })
    const api = setup({ addComicsPanel: vi.fn().mockResolvedValue(added), comicsAddability: addability })
    await search()
    await userEvent.click(await screen.findByRole("button", { name: "Add m1.jpg" }))
    expect(api.addComicsPanel).toHaveBeenCalledWith("c1", "m1")
    expect(await screen.findByLabelText("Remove panel 3")).toBeInTheDocument()
    expect(await screen.findByText("added")).toBeInTheDocument()
  })

  it("moves a panel up and down sending the exact new order", async () => {
    const reorder = vi.fn().mockResolvedValueOnce(detail(["p2", "p1", "p3"])).mockResolvedValueOnce(detail(["p2", "p3", "p1"]))
    const api = setup({ getComics: vi.fn().mockResolvedValue(detail(["p1", "p2", "p3"])), reorderComicsPanels: reorder })
    await userEvent.click(await screen.findByLabelText("Move panel 2 up"))
    await waitFor(() => expect(api.reorderComicsPanels).toHaveBeenLastCalledWith("c1", ["p2", "p1", "p3"]))
    await userEvent.click(await screen.findByLabelText("Move panel 2 down"))
    await waitFor(() => expect(api.reorderComicsPanels).toHaveBeenLastCalledWith("c1", ["p2", "p3", "p1"]))
  })

  it("removes a panel and re-fetches the comics", async () => {
    const getComics = vi.fn().mockResolvedValueOnce(detail(["p1", "p2"])).mockResolvedValue(detail(["p2"]))
    const api = setup({ getComics })
    await userEvent.click(await screen.findByLabelText("Remove panel 1"))
    expect(api.removeComicsPanel).toHaveBeenCalledWith("c1", "p1")
    await waitFor(() => expect(screen.queryByText("p1.jpg")).not.toBeInTheDocument())
    expect(getComics).toHaveBeenCalledTimes(2)
  })

  it("shows disabled add with the reason and a link for an image in another comics", async () => {
    setup({
      searchMemes: vi.fn().mockResolvedValue({ items: [
        { id: "m1", imageUrl: "/i/m1", originalFileName: "m1.jpg" },
        { id: "m2", imageUrl: "/i/m2", originalFileName: "m2.jpg" },
        { id: "m3", imageUrl: "/i/m3", originalFileName: "m3.jpg" },
      ], hasNext: false }),
      comicsAddability: vi.fn().mockResolvedValue({ items: [
        { imageId: "m1", addable: false, reason: "in_comics", comicsId: "other", comicsName: "Other one" },
        { imageId: "m2", addable: false, reason: "flagged", comicsId: null, comicsName: null },
        { imageId: "m3", addable: false, reason: "live_cut", comicsId: null, comicsName: null },
      ] }),
    })
    await search()
    expect(await screen.findByRole("button", { name: "Add m1.jpg" })).toBeDisabled()
    expect(screen.getByRole("link", { name: "Other one" })).toHaveAttribute("href", "/admin/comics/other")
    expect(screen.getByRole("button", { name: "Add m2.jpg" })).toBeDisabled()
    expect(screen.getByText("Flagged")).toBeInTheDocument()
    expect(screen.getByText("Has a recut cut")).toBeInTheDocument()
  })

  it("add conflict refreshes addability and shows no error", async () => {
    const addability = vi.fn().mockResolvedValueOnce({ items: [free("m1")] })
      .mockResolvedValue({ items: [{ imageId: "m1", addable: false, reason: "in_comics", comicsId: "other", comicsName: "Other one" }] })
    setup({
      comicsAddability: addability,
      addComicsPanel: vi.fn().mockRejectedValue(new ComicsRequestError(409, { code: "image_in_comics", comicsName: "Other one" })),
    })
    await search()
    await userEvent.click(await screen.findByRole("button", { name: "Add m1.jpg" }))
    expect(await screen.findByRole("link", { name: "Other one" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Add m1.jpg" })).toBeDisabled()
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
  })

  it("other add errors are shown in words", async () => {
    setup({ addComicsPanel: vi.fn().mockRejectedValue(new ComicsRequestError(409, { code: "built" })) })
    await search()
    await userEvent.click(await screen.findByRole("button", { name: "Add m1.jpg" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("built")
  })

  it("pages results with the cursor", async () => {
    const searchMemes = vi.fn()
      .mockResolvedValueOnce({ items: [{ id: "m1", imageUrl: "/i/m1", originalFileName: "m1.jpg" }], hasNext: true, nextCursor: "cur1" })
      .mockResolvedValueOnce({ items: [{ id: "m2", imageUrl: "/i/m2", originalFileName: "m2.jpg" }], hasNext: false })
    const api = setup({ searchMemes, comicsAddability: vi.fn().mockResolvedValue({ items: [free("m1"), free("m2")] }) })
    await search()
    await userEvent.click(await screen.findByRole("button", { name: "Show more" }))
    expect(await screen.findByRole("button", { name: "Add m2.jpg" })).toBeInTheDocument()
    expect(api.searchMemes).toHaveBeenLastCalledWith(expect.objectContaining({ query: "cat", cursor: "cur1", record: false }))
    expect(api.comicsAddability).toHaveBeenLastCalledWith(["m2"])
  })

  it("renames inline and reports errors", async () => {
    const renameComics = vi.fn().mockRejectedValueOnce(new ComicsRequestError(409, { code: "name_taken" }))
      .mockResolvedValue(detail(["p1", "p2"], { name: "Fresh" }))
    setup({ renameComics })
    await userEvent.click(await screen.findByRole("button", { name: "Rename" }))
    const input = screen.getByLabelText("Comics name")
    await userEvent.clear(input)
    await userEvent.type(input, "Fresh")
    await userEvent.click(screen.getByRole("button", { name: "Save name" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("already exists")
    await userEvent.click(screen.getByRole("button", { name: "Save name" }))
    expect(await screen.findByRole("heading", { name: "Fresh" })).toBeInTheDocument()
    expect(renameComics).toHaveBeenLastCalledWith("c1", "Fresh")
  })
})

describe("ComicsEditorPage preview", () => {
  it("requests the preview and bumps the version 400 ms after a change", async () => {
    vi.useFakeTimers()
    try {
      const reorder = vi.fn().mockResolvedValue(detail(["p2", "p1"]))
      const api = setup({ reorderComicsPanels: reorder })
      await act(async () => { await vi.advanceTimersByTimeAsync(0) })
      const img = screen.getByAltText("Comics preview")
      const first = img.getAttribute("src")
      expect(first).toContain("w=1200")
      fireEvent.click(screen.getByLabelText("Move panel 2 up"))
      await act(async () => { await vi.advanceTimersByTimeAsync(0) })
      expect(screen.getByAltText("Comics preview").getAttribute("src")).toBe(first)
      await act(async () => { await vi.advanceTimersByTimeAsync(400) })
      expect(screen.getByAltText("Comics preview").getAttribute("src")).not.toBe(first)
      expect(api.comicsPreviewUrl).toHaveBeenCalledWith("c1", 1200, expect.any(String))
    } finally {
      vi.useRealTimers()
    }
  })

  it("explains a failed preview with the panel number", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false, status: 422,
      json: async () => ({ detail: { code: "compose_failed", reason: "unreadable", position: 2 } }),
    })
    vi.stubGlobal("fetch", fetchMock)
    try {
      setup()
      fireEvent.error(await screen.findByAltText("Comics preview"))
      expect(await screen.findByRole("alert")).toHaveTextContent("Panel 2 can't be read")
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it("an empty draft shows an empty state and requests no preview", async () => {
    const api = setup({ getComics: vi.fn().mockResolvedValue(detail([])) })
    expect(await screen.findByText(/no panels yet/i)).toBeInTheDocument()
    expect(api.comicsPreviewUrl).not.toHaveBeenCalled()
    expect(screen.queryByAltText("Comics preview")).not.toBeInTheDocument()
  })
})

describe("ComicsEditorPage built", () => {
  it("is read-only with provenance and a link to the result", async () => {
    const api = setup({
      getComics: vi.fn().mockResolvedValue(detail(["p1", "p2"], { state: "built", builtImageId: "built-1" })),
    })
    const result = await screen.findByAltText("Built comics")
    expect(result).toHaveAttribute("src", "http://example.com/api/images/built-1")
    expect(screen.getByRole("link", { name: "Open the meme" })).toHaveAttribute("href", "/memes/built-1")
    const list = screen.getByRole("list", { name: "Source panels" })
    const items = within(list).getAllByRole("listitem")
    expect(items[0]).toHaveTextContent("1")
    expect(items[0]).toHaveTextContent("p1.jpg")
    expect(items[1]).toHaveTextContent("2")
    expect(screen.queryByRole("button", { name: "Build" })).not.toBeInTheDocument()
    expect(screen.queryByLabelText("Search memes")).not.toBeInTheDocument()
    expect(screen.queryByLabelText("Remove panel 1")).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Rename" })).not.toBeInTheDocument()
    expect(api.comicsPreviewUrl).not.toHaveBeenCalled()
  })
})
