import { render, screen, waitFor } from "@testing-library/react"
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
    await userEvent.type(screen.getByRole("searchbox"), "zeb")
    expect(await screen.findByRole("button", { name: /Zebra/ })).toBeInTheDocument()
    expect(listComics).toHaveBeenLastCalledWith("zeb", 0, 20)
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
})
