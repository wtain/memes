import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { StrictMode } from "react"
import { MemoryRouter } from "react-router-dom"
import { MemeComicsBlock } from "./MemeComicsBlock"
import { ComicsRequestError } from "../../api/errors"
import { makeMockApi } from "../../test/mockApi"
import type { ComicsDetail, ComicsSummary } from "../../types/generated/all"

function row(id: string, over: Partial<ComicsSummary> = {}): ComicsSummary {
  return { id, name: `Comics ${id}`, state: "draft", panelCount: 2, coverImageId: null,
    createdAt: "2026-11-01T00:00:00Z", builtAt: null, ...over }
}

function detail(id: string, imageId: string, position = 0, over: Partial<ComicsDetail> = {}): ComicsDetail {
  return { id, name: `Comics ${id}`, state: "draft", builtImageId: null, builtAt: null,
    panels: [{ id: "p1", position, imageId } as ComicsDetail["panels"][number]], ...over }
}

function setup(overrides: Parameters<typeof makeMockApi>[0] = {}) {
  const api = makeMockApi({
    listComics: vi.fn().mockResolvedValue({ items: [row("a"), row("b", { state: "built" })], hasMore: false }),
    ...overrides,
  })
  render(<StrictMode><MemoryRouter><MemeComicsBlock memesApi={api} imageId="img1" /></MemoryRouter></StrictMode>)
  return api
}

describe("MemeComicsBlock", () => {
  it("shows the comics and 1-based panel number with a link to the editor", async () => {
    setup({ comicsByImage: vi.fn().mockResolvedValue({ comics: { id: "c1", name: "Cats", state: "draft", position: 2 } }) })
    const link = await screen.findByRole("link", { name: "Cats" })
    expect(link).toHaveAttribute("href", "/admin/comics/c1")
    expect(screen.getByTestId("comics-membership")).toHaveTextContent("In comics Cats (panel 3)")
    expect(screen.queryByRole("button", { name: /Add to comics/ })).not.toBeInTheDocument()
  })

  it("shows the same text for a built comics", async () => {
    setup({ comicsByImage: vi.fn().mockResolvedValue({ comics: { id: "c1", name: "Cats", state: "built", position: 0 } }) })
    expect(await screen.findByTestId("comics-membership")).toHaveTextContent("In comics Cats (panel 1)")
  })

  it("offers Add to comics with no error when the image is in none", async () => {
    const api = setup()
    expect(await screen.findByRole("button", { name: "Add to comics…" })).toBeInTheDocument()
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
    expect(api.comicsByImage).toHaveBeenCalledTimes(1)
  })

  it("tolerates a malformed result", async () => {
    setup({ comicsByImage: vi.fn().mockResolvedValue({}) })
    expect(await screen.findByRole("button", { name: "Add to comics…" })).toBeInTheDocument()
  })

  it("renders nothing harmful when the lookup fails", async () => {
    const api = setup({ comicsByImage: vi.fn().mockRejectedValue(new Error("x")) })
    await waitFor(() => expect(api.comicsByImage).toHaveBeenCalled())
    expect(screen.queryByRole("button", { name: "Add to comics…" })).not.toBeInTheDocument()
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
  })

  it("lists only draft comics in the picker", async () => {
    setup()
    await userEvent.click(await screen.findByRole("button", { name: "Add to comics…" }))
    expect(await screen.findByRole("button", { name: /Comics a/ })).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /Comics b/ })).not.toBeInTheDocument()
  })

  it("adds to a picked draft, shows a confirmation with the editor link and the new state", async () => {
    const api = setup({ addComicsPanel: vi.fn().mockResolvedValue(detail("a", "img1", 4)) })
    await userEvent.click(await screen.findByRole("button", { name: "Add to comics…" }))
    await userEvent.click(await screen.findByRole("button", { name: /Comics a/ }))
    expect(api.addComicsPanel).toHaveBeenCalledWith("a", "img1")
    expect(await screen.findByText(/Added to/)).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "Open the editor" })).toHaveAttribute("href", "/admin/comics/a")
    await userEvent.click(screen.getByRole("button", { name: "Close" }))
    expect(screen.getByTestId("comics-membership")).toHaveTextContent("In comics Comics a (panel 5)")
  })

  it("searches by name after a debounce and shows more", async () => {
    const listComics = vi.fn((q: string, offset: number) => Promise.resolve(
      q === "zeb" ? { items: [row("z", { name: "Zebra" })], hasMore: false }
        : offset === 0 ? { items: [row("a")], hasMore: true }
          : { items: [row("c")], hasMore: false }))
    setup({ listComics })
    await userEvent.click(await screen.findByRole("button", { name: "Add to comics…" }))
    await screen.findByRole("button", { name: /Comics a/ })
    await userEvent.click(screen.getByRole("button", { name: "Show more" }))
    expect(await screen.findByRole("button", { name: /Comics c/ })).toBeInTheDocument()
    expect(listComics).toHaveBeenLastCalledWith("", 1, 20)
    vi.useFakeTimers()
    try {
      fireEvent.change(screen.getByRole("searchbox"), { target: { value: "zeb" } })
      await act(async () => { await vi.advanceTimersByTimeAsync(299) })
      expect(listComics).not.toHaveBeenCalledWith("zeb", 0, 20)
      await act(async () => { await vi.advanceTimersByTimeAsync(1) })
      expect(listComics).toHaveBeenLastCalledWith("zeb", 0, 20)
    } finally {
      vi.useRealTimers()
    }
    expect(await screen.findByRole("button", { name: /Zebra/ })).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /Comics a/ })).not.toBeInTheDocument()
  })

  it("shows a bad new name in words and keeps Cyrillic unchanged", async () => {
    const createComics = vi.fn()
      .mockRejectedValueOnce(new ComicsRequestError(422, { code: "name_invalid", reason: "forbidden_character" }))
      .mockResolvedValueOnce(detail("n", "x", 0, { name: "Кошки" }))
    const api = setup({ createComics, addComicsPanel: vi.fn().mockResolvedValue(detail("n", "img1", 0, { name: "Кошки" })) })
    await userEvent.click(await screen.findByRole("button", { name: "Add to comics…" }))
    await userEvent.click(await screen.findByRole("button", { name: "New comics" }))
    await userEvent.type(screen.getByLabelText("Name"), "bad?")
    await userEvent.click(screen.getByRole("button", { name: "Create and add" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("The name contains a character that is not allowed")
    await userEvent.clear(screen.getByLabelText("Name"))
    await userEvent.type(screen.getByLabelText("Name"), "Кошки")
    await userEvent.click(screen.getByRole("button", { name: "Create and add" }))
    expect(await screen.findByText(/Added to/)).toBeInTheDocument()
    expect(createComics).toHaveBeenLastCalledWith("Кошки")
    expect(api.addComicsPanel).toHaveBeenCalledWith("n", "img1")
  })

  it("offers Move from X after image_in_comics and moves", async () => {
    const addComicsPanel = vi.fn()
      .mockRejectedValueOnce(new ComicsRequestError(409, { code: "image_in_comics", comicsId: "z", comicsName: "Zed" }))
      .mockResolvedValueOnce(detail("a", "img1", 1))
    setup({ addComicsPanel })
    await userEvent.click(await screen.findByRole("button", { name: "Add to comics…" }))
    await userEvent.click(await screen.findByRole("button", { name: /Comics a/ }))
    expect(await screen.findByRole("alert")).toHaveTextContent('already a panel of the comics "Zed"')
    await userEvent.click(screen.getByRole("button", { name: "Move from Zed" }))
    expect(addComicsPanel).toHaveBeenLastCalledWith("a", "img1", true)
    expect(await screen.findByText(/Added to/)).toBeInTheDocument()
  })

  it("shows other add errors in words", async () => {
    setup({ addComicsPanel: vi.fn().mockRejectedValue(new ComicsRequestError(409, { code: "built" })) })
    await userEvent.click(await screen.findByRole("button", { name: "Add to comics…" }))
    await userEvent.click(await screen.findByRole("button", { name: /Comics a/ }))
    expect(await screen.findByRole("alert")).toHaveTextContent("built and can't be changed")
    expect(screen.queryByRole("button", { name: /Move from/ })).not.toBeInTheDocument()
  })

  it("shows the reason instead of the button when the image cannot be added", async () => {
    const api = setup({
      comicsAddability: vi.fn().mockResolvedValue({
        items: [{ imageId: "img1", addable: false, reason: "flagged", comicsId: null, comicsName: null }],
      }),
    })
    expect(await screen.findByTestId("comics-not-addable")).toHaveTextContent("That image is flagged")
    expect(api.comicsAddability).toHaveBeenCalledWith(["img1"])
    expect(screen.queryByRole("button", { name: "Add to comics…" })).not.toBeInTheDocument()
  })

  it("treats a failed addability lookup as addable", async () => {
    setup({ comicsAddability: vi.fn().mockRejectedValue(new Error("x")) })
    expect(await screen.findByRole("button", { name: "Add to comics…" })).toBeInTheDocument()
  })

  it("does not ask about addability when the image is already in a comics", async () => {
    const api = setup({ comicsByImage: vi.fn().mockResolvedValue({ comics: { id: "c1", name: "Cats", state: "draft", position: 0 } }) })
    await screen.findByTestId("comics-membership")
    expect(api.comicsAddability).not.toHaveBeenCalled()
  })

  it("retries a failed add on the comics it just created, without creating another", async () => {
    const createComics = vi.fn().mockResolvedValue(detail("n", "x", 0, { name: "Fresh" }))
    const addComicsPanel = vi.fn()
      .mockRejectedValueOnce(new ComicsRequestError(422, { code: "image_not_addable", reason: "flagged" }))
      .mockResolvedValueOnce(detail("n", "img1", 0, { name: "Fresh" }))
    setup({ createComics, addComicsPanel })
    await userEvent.click(await screen.findByRole("button", { name: "Add to comics…" }))
    await userEvent.click(await screen.findByRole("button", { name: "New comics" }))
    await userEvent.type(screen.getByLabelText("Name"), "Fresh")
    await userEvent.click(screen.getByRole("button", { name: "Create and add" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("That image is flagged")
    await userEvent.click(screen.getByRole("button", { name: "Create and add" }))
    expect(await screen.findByText(/Added to/)).toBeInTheDocument()
    expect(createComics).toHaveBeenCalledTimes(1)
    expect(addComicsPanel).toHaveBeenCalledTimes(2)
    expect(addComicsPanel).toHaveBeenLastCalledWith("n", "img1")
  })

  it("offers Move from X when the add after a create fails with image_in_comics", async () => {
    const addComicsPanel = vi.fn()
      .mockRejectedValueOnce(new ComicsRequestError(409, { code: "image_in_comics", comicsId: "z", comicsName: "Zed" }))
      .mockResolvedValueOnce(detail("n", "img1", 0))
    const createComics = vi.fn().mockResolvedValue(detail("n", "x", 0))
    setup({ createComics, addComicsPanel })
    await userEvent.click(await screen.findByRole("button", { name: "Add to comics…" }))
    await userEvent.click(await screen.findByRole("button", { name: "New comics" }))
    await userEvent.type(screen.getByLabelText("Name"), "Fresh")
    await userEvent.click(screen.getByRole("button", { name: "Create and add" }))
    await userEvent.click(await screen.findByRole("button", { name: "Move from Zed" }))
    expect(addComicsPanel).toHaveBeenLastCalledWith("n", "img1", true)
    expect(createComics).toHaveBeenCalledTimes(1)
    expect(await screen.findByText(/Added to/)).toBeInTheDocument()
  })

  it("keeps reading pages until one has a draft", async () => {
    const listComics = vi.fn((_q: string, offset: number) => Promise.resolve(
      offset === 0 ? { items: [row("b1", { state: "built" })], hasMore: true }
        : offset === 1 ? { items: [row("b2", { state: "built" })], hasMore: true }
          : { items: [row("d")], hasMore: false }))
    setup({ listComics })
    await userEvent.click(await screen.findByRole("button", { name: "Add to comics…" }))
    expect(await screen.findByRole("button", { name: /Comics d/ })).toBeInTheDocument()
    expect(screen.queryByText("No draft comics found")).not.toBeInTheDocument()
    // StrictMode's first mount adds one cancelled request for offset 0
    expect(new Set(listComics.mock.calls.map(([, offset]) => offset))).toEqual(new Set([0, 1, 2]))
    expect(listComics).toHaveBeenLastCalledWith("", 2, 20)
  })

  it("stops after 10 pages without a draft and leaves Show more", async () => {
    const listComics = vi.fn((_q: string, offset: number) => Promise.resolve(
      { items: [row(`b${offset}`, { state: "built" })], hasMore: true }))
    setup({ listComics })
    await userEvent.click(await screen.findByRole("button", { name: "Add to comics…" }))
    expect(await screen.findByRole("button", { name: "Show more" })).toBeInTheDocument()
    const offsets = new Set(listComics.mock.calls.map(([, offset]) => offset))
    expect(offsets).toEqual(new Set([0, 1, 2, 3, 4, 5, 6, 7, 8, 9]))
  })

  it("clears the error and the move offer when switching between existing and new", async () => {
    setup({
      addComicsPanel: vi.fn().mockRejectedValue(new ComicsRequestError(409, { code: "image_in_comics", comicsId: "z", comicsName: "Zed" })),
    })
    await userEvent.click(await screen.findByRole("button", { name: "Add to comics…" }))
    await userEvent.click(await screen.findByRole("button", { name: /Comics a/ }))
    expect(await screen.findByRole("alert")).toBeInTheDocument()
    await userEvent.click(screen.getByRole("button", { name: "New comics" }))
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /Move from/ })).not.toBeInTheDocument()
  })
})
