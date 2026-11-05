import { act, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import BuildDialog from "./BuildDialog"
import { ComicsRequestError } from "../../api/errors"
import { makeMockApi } from "../../test/mockApi"
import type { ComicsDetail, ComicsPanel } from "../../types/generated/all"

function panel(id: string, position: number): ComicsPanel {
  return { id, position, imageId: `img-${id}`, sourceFilename: `${id}.jpg`, kept: false, sourceImageId: `img-${id}` }
}
const comics: ComicsDetail = { id: "c1", name: "My comics", state: "draft", builtImageId: null, builtAt: null,
  panels: [panel("p1", 0), panel("p2", 1), panel("p3", 2)] }
const built = { comics: { ...comics, state: "built" as const, builtImageId: "b1" }, builtImageId: "b1" }

function setup(over: Parameters<typeof makeMockApi>[0] = {}) {
  const api = makeMockApi({
    comicsPreviewUrl: vi.fn((id: string, w: number, v: string) => `http://x/api/comics/${id}/preview?w=${w}&v=${v}`),
    buildComics: vi.fn().mockResolvedValue(built),
    ...over,
  })
  const onClose = vi.fn()
  const onBuilt = vi.fn()
  render(<BuildDialog memesApi={api} comics={comics} onClose={onClose} onBuilt={onBuilt} />)
  return { api, onClose, onBuilt }
}

describe("BuildDialog", () => {
  it("shows the preview, the irreversible sentence and unchecked keep boxes", () => {
    setup()
    expect(screen.getByAltText("Comics preview")).toBeInTheDocument()
    expect(screen.getByText(/This cannot be undone\./)).toBeInTheDocument()
    const boxes = screen.getAllByRole("checkbox", { name: /keep in library/i })
    expect(boxes).toHaveLength(3)
    boxes.forEach((b) => expect(b).not.toBeChecked())
  })

  it("sends exactly the checked panels", async () => {
    const { api, onBuilt } = setup()
    const boxes = screen.getAllByRole("checkbox", { name: /keep in library/i })
    await userEvent.click(boxes[0])
    await userEvent.click(boxes[2])
    await userEvent.click(screen.getByRole("button", { name: "Build comics" }))
    expect(api.buildComics).toHaveBeenCalledWith("c1", ["p1", "p3"])
    expect(onBuilt).toHaveBeenCalledWith(built)
  })

  it("sends an empty keep list when nothing is checked", async () => {
    const { api } = setup()
    await userEvent.click(screen.getByRole("button", { name: "Build comics" }))
    expect(api.buildComics).toHaveBeenCalledWith("c1", [])
  })

  it("a double click sends one request and the button is disabled meanwhile", async () => {
    let resolve!: (v: typeof built) => void
    const pending = new Promise<typeof built>((r) => { resolve = r })
    const { api } = setup({ buildComics: vi.fn().mockReturnValue(pending) })
    const button = screen.getByRole("button", { name: "Build comics" })
    act(() => { button.click(); button.click() })
    expect(api.buildComics).toHaveBeenCalledTimes(1)
    expect(button).toBeDisabled()
    await act(async () => { resolve(built) })
  })

  it.each([
    [{ code: "not_buildable", reason: "too_few_panels" }, "at least two panels"],
    [{ code: "compose_failed", reason: "unreadable", position: 2 }, "Panel 2 can't be read"],
    [{ code: "built" }, "built and can't be changed"],
    [{ code: "keep_invalid" }, "marked to keep"],
  ])("shows %j in the dialog", async (detail, text) => {
    const { onBuilt } = setup({ buildComics: vi.fn().mockRejectedValue(new ComicsRequestError(409, detail)) })
    await userEvent.click(screen.getByRole("button", { name: "Build comics" }))
    expect(await screen.findByRole("alert")).toHaveTextContent(text)
    expect(onBuilt).not.toHaveBeenCalled()
    expect(screen.getByRole("button", { name: "Build comics" })).toBeEnabled()
  })

  it("Cancel closes", async () => {
    const { onClose } = setup()
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }))
    expect(onClose).toHaveBeenCalled()
  })

  it("is inert while the build runs and works again after an error", async () => {
    let reject!: (e: unknown) => void
    const pending = new Promise<typeof built>((_, r) => { reject = r })
    const { onClose } = setup({ buildComics: vi.fn().mockReturnValue(pending) })
    await userEvent.click(screen.getByRole("button", { name: "Build comics" }))
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled()
    await userEvent.keyboard("{Escape}")
    await userEvent.click(screen.getByText("Build comics", { selector: "h2" }).closest(".fixed")!)
    await userEvent.click(screen.getByRole("button", { name: "✕" }))
    expect(onClose).not.toHaveBeenCalled()

    await act(async () => { reject(new ComicsRequestError(409, { code: "keep_invalid" })) })
    expect(await screen.findByRole("alert")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Cancel" })).toBeEnabled()
    await userEvent.keyboard("{Escape}")
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
