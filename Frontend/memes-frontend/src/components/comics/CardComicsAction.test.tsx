import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { CardComicsAction } from "./CardComicsAction"
import { WorkingComicsBar } from "./WorkingComicsBar"
import { ComicsRequestError } from "../../api/errors"
import { clearWorkingComics, getWorkingComics, setWorkingComics } from "../../comics/workingComics"
import { makeMockApi } from "../../test/mockApi"
import type { ComicsDetail, ComicsSummary } from "../../types/generated/all"

function row(id: string): ComicsSummary {
  return { id, name: `Comics ${id}`, state: "draft", panelCount: 2, coverImageId: null,
    createdAt: "2026-11-01T00:00:00Z", builtAt: null }
}

function detail(id: string, imageId: string): ComicsDetail {
  return { id, name: `Comics ${id}`, state: "draft", builtImageId: null, builtAt: null,
    panels: [{ id: "p1", position: 0, imageId } as ComicsDetail["panels"][number]] }
}

function setup(overrides: Parameters<typeof makeMockApi>[0] = {}) {
  const api = makeMockApi({
    listComics: vi.fn().mockResolvedValue({ items: [row("a")], hasMore: false }),
    addComicsPanel: vi.fn().mockImplementation((id: string, imageId: string) => Promise.resolve(detail(id, imageId))),
    ...overrides,
  })
  render(<MemoryRouter><WorkingComicsBar /><CardComicsAction memesApi={api} imageId="img1" /></MemoryRouter>)
  return api
}

afterEach(() => clearWorkingComics())

describe("CardComicsAction", () => {
  it("opens the picker without a working comics and sets it from the result", async () => {
    const api = setup()
    await userEvent.click(screen.getByRole("button", { name: "Add to comics" }))
    await userEvent.click(await screen.findByRole("button", { name: /Comics a/ }))
    expect(api.addComicsPanel).toHaveBeenCalledWith("a", "img1")
    expect(getWorkingComics()).toEqual({ id: "a", name: "Comics a" })
    expect(await screen.findByText(/Adding to/)).toBeInTheDocument()
  })

  it("adds directly to the working comics and shows the added state", async () => {
    setWorkingComics({ id: "w", name: "Work" })
    const api = setup()
    await userEvent.click(screen.getByRole("button", { name: "Add to Work" }))
    expect(api.addComicsPanel).toHaveBeenCalledWith("w", "img1")
    expect(api.listComics).not.toHaveBeenCalled()
    expect(await screen.findByText(/Added to Work/)).toBeInTheDocument()
    expect(screen.getAllByRole("link", { name: "Open editor" }).some(l => l.getAttribute("href") === "/admin/comics/w")).toBe(true)
    expect(screen.queryByRole("button", { name: "Add to Work" })).not.toBeInTheDocument()
  })

  it("shows an error and keeps the button", async () => {
    setWorkingComics({ id: "w", name: "Work" })
    setup({ addComicsPanel: vi.fn().mockRejectedValue(new ComicsRequestError(409, { code: "image_in_comics", comicsName: "Cats" })) })
    await userEvent.click(screen.getByRole("button", { name: "Add to Work" }))
    expect(await screen.findByRole("alert")).toHaveTextContent('already a panel of the comics "Cats"')
    expect(screen.getByRole("button", { name: "Add to Work" })).toBeEnabled()
    expect(getWorkingComics()).not.toBeNull()
  })

  it.each(["built", "not_found"])("clears the working comics on %s", async (code) => {
    setWorkingComics({ id: "w", name: "Work" })
    setup({ addComicsPanel: vi.fn().mockRejectedValue(new ComicsRequestError(409, { code })) })
    await userEvent.click(screen.getByRole("button", { name: "Add to Work" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("pick another comics")
    await waitFor(() => expect(getWorkingComics()).toBeNull())
    expect(screen.getByRole("button", { name: "Add to comics" })).toBeInTheDocument()
  })
})

describe("WorkingComicsBar", () => {
  it("shows nothing without a working comics", () => {
    render(<MemoryRouter><WorkingComicsBar /></MemoryRouter>)
    expect(screen.queryByText(/Adding to/)).not.toBeInTheDocument()
  })

  it("links to the editor, and Change and Stop clear it", async () => {
    setWorkingComics({ id: "w", name: "Work" })
    render(<MemoryRouter><WorkingComicsBar /></MemoryRouter>)
    expect(screen.getByRole("link", { name: "Open editor" })).toHaveAttribute("href", "/admin/comics/w")
    await userEvent.click(screen.getByRole("button", { name: "Change" }))
    expect(screen.queryByText(/Adding to/)).not.toBeInTheDocument()
    setWorkingComics({ id: "w", name: "Work" })
    await userEvent.click(await screen.findByRole("button", { name: "Stop" }))
    expect(getWorkingComics()).toBeNull()
  })
})
