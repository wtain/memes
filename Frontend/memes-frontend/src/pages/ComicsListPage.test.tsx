import { act, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import ComicsListPage from "./ComicsListPage"
import { ComicsRequestError } from "../api/errors"
import { makeMockApi } from "../test/mockApi"
import type { ComicsSummary } from "../types/generated/all"

function row(id: string, over: Partial<ComicsSummary> = {}): ComicsSummary {
  return { id, name: `Comics ${id}`, state: "draft", panelCount: 2, coverImageId: `img-${id}`,
    createdAt: "2026-11-01T00:00:00Z", builtAt: null, ...over }
}

function setup(overrides: Parameters<typeof makeMockApi>[0] = {}) {
  const api = makeMockApi({
    listComics: vi.fn().mockResolvedValue({ items: [row("a"), row("b", { state: "built", builtAt: "2026-11-02T00:00:00Z" })], hasMore: false }),
    ...overrides,
  })
  render(<MemoryRouter><ComicsListPage memesApi={api} /></MemoryRouter>)
  return api
}

describe("ComicsListPage", () => {
  it("renders rows with name, state, panel count, cover and link", async () => {
    setup()
    const link = await screen.findByRole("link", { name: /Comics a/ })
    expect(link).toHaveAttribute("href", "/admin/comics/a")
    expect(screen.getByText("Comics b")).toBeInTheDocument()
    expect(screen.getByText("draft")).toBeInTheDocument()
    expect(screen.getByText("built")).toBeInTheDocument()
    expect(screen.getAllByText("2 panels")).toHaveLength(2)
    expect(screen.getByAltText("Cover of Comics a")).toHaveAttribute("src", "http://example.com/api/images/img-a")
  })

  it("shows an empty state", async () => {
    setup({ listComics: vi.fn().mockResolvedValue({ items: [], hasMore: false }) })
    expect(await screen.findByText("No comics yet")).toBeInTheDocument()
  })

  it("shows an error with retry when loading fails", async () => {
    const listComics = vi.fn().mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValue({ items: [row("a")], hasMore: false })
    setup({ listComics })
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load the comics")
    await userEvent.click(screen.getByRole("button", { name: "Retry" }))
    expect(await screen.findByText("Comics a")).toBeInTheDocument()
  })

  it("searches by name after a 300 ms debounce", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      const api = setup()
      await screen.findByText("Comics a")
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
      await user.type(screen.getByLabelText("Search comics"), "cat")
      expect(api.listComics).toHaveBeenCalledTimes(1)
      await act(async () => { await vi.advanceTimersByTimeAsync(300) })
      await waitFor(() => expect(api.listComics).toHaveBeenLastCalledWith("cat", 0, expect.any(Number)))
      expect(api.listComics).toHaveBeenCalledTimes(2)
    } finally {
      vi.useRealTimers()
    }
  })

  it("shows more by appending the next page at the offset of the loaded items", async () => {
    const listComics = vi.fn()
      .mockResolvedValueOnce({ items: [row("a"), row("b")], hasMore: true })
      .mockResolvedValueOnce({ items: [row("c")], hasMore: false })
    setup({ listComics })
    await userEvent.click(await screen.findByRole("button", { name: "Show more" }))
    expect(await screen.findByText("Comics c")).toBeInTheDocument()
    expect(listComics).toHaveBeenLastCalledWith("", 2, expect.any(Number))
    expect(screen.getByText("Comics a")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Show more" })).not.toBeInTheDocument()
  })

  it("has no Show more button when hasMore is false", async () => {
    setup()
    await screen.findByText("Comics a")
    expect(screen.queryByRole("button", { name: "Show more" })).not.toBeInTheDocument()
  })

  it("deletes a draft only after an inline confirmation, and offers no delete on built rows", async () => {
    const api = setup()
    await screen.findByText("Comics a")
    expect(screen.getAllByRole("button", { name: /^Delete/ })).toHaveLength(1)
    await userEvent.click(screen.getByRole("button", { name: "Delete Comics a" }))
    expect(api.deleteComics).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole("button", { name: "No" }))
    expect(api.deleteComics).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole("button", { name: "Delete Comics a" }))
    await userEvent.click(screen.getByRole("button", { name: "Yes" }))
    expect(api.deleteComics).toHaveBeenCalledWith("a")
    await waitFor(() => expect(screen.queryByText("Comics a")).not.toBeInTheDocument())
    expect(screen.getByText("Comics b")).toBeInTheDocument()
  })

  it("shows the reason when a delete fails", async () => {
    setup({ deleteComics: vi.fn().mockRejectedValue(new ComicsRequestError(409, { code: "built" })) })
    await userEvent.click(await screen.findByRole("button", { name: "Delete Comics a" }))
    await userEvent.click(screen.getByRole("button", { name: "Yes" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("This comics is built and can't be changed")
  })

  describe("creating", () => {
    async function openForm() {
      await screen.findByText("Comics a")
      await userEvent.click(screen.getByRole("button", { name: "New comics" }))
    }

    it("creates and prepends the new draft, and sends a Cyrillic name unchanged", async () => {
      const createComics = vi.fn().mockResolvedValue({ id: "n", name: "Мой комикс", state: "draft", panels: [], builtImageId: null, builtAt: null })
      const api = setup({ createComics })
      await openForm()
      await userEvent.type(screen.getByLabelText("Name"), "  Мой комикс ")
      await userEvent.click(screen.getByRole("button", { name: "Create" }))
      expect(api.createComics).toHaveBeenCalledWith("Мой комикс")
      expect(await screen.findByRole("link", { name: /Мой комикс/ })).toHaveAttribute("href", "/admin/comics/n")
    })

    it.each([
      [422, { code: "name_invalid", reason: "empty" }, "The name can't be empty"],
      [422, { code: "name_invalid", reason: "too_long" }, "The name is too long"],
      [422, { code: "name_invalid", reason: "forbidden_character" }, "The name contains a character that is not allowed"],
      [422, { code: "name_invalid", reason: "trailing_dot_or_space" }, "The name can't end with a dot or a space"],
      [422, { code: "name_invalid", reason: "reserved_name" }, "That name is reserved"],
      [409, { code: "name_taken" }, "A comics with that name already exists"],
    ])("explains a rejected name (%s %j)", async (status, detail, text) => {
      const api = setup({ createComics: vi.fn().mockRejectedValue(new ComicsRequestError(status, detail)) })
      await openForm()
      await userEvent.type(screen.getByLabelText("Name"), "Привет")
      await userEvent.click(screen.getByRole("button", { name: "Create" }))
      expect(await screen.findByRole("alert")).toHaveTextContent(text)
      expect(api.createComics).toHaveBeenCalledWith("Привет")
    })
  })
})
