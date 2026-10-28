import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, useLocation } from "react-router-dom"
import RecutReviewPage from "./RecutReviewPage"
import { CuratorRequestError } from "../api/errors"
import { makeProposalDetail, makeProposalPage } from "../recut/fixtures"
import { makeMockApi } from "../test/mockApi"

function Where() {
  const location = useLocation()
  return <div data-testid="where">{location.search}</div>
}

function setup(overrides: Parameters<typeof makeMockApi>[0] = {}, entry = "/admin/recut") {
  const api = makeMockApi({
    listRecutProposals: vi.fn().mockResolvedValue(makeProposalPage(["a", "b", "c"], 3)),
    getRecutProposal: vi.fn((id: string) => Promise.resolve(makeProposalDetail(id, {
      previousId: id === "a" ? null : id === "b" ? "a" : "b",
      nextId: id === "a" ? "b" : id === "b" ? "c" : null,
    }))),
    ...overrides,
  })
  render(
    <MemoryRouter initialEntries={[entry]}>
      <RecutReviewPage memesApi={api} />
      <Where />
    </MemoryRouter>,
  )
  return api
}

describe("RecutReviewPage", () => {
  it("shows an empty queue", async () => {
    setup({ listRecutProposals: vi.fn().mockResolvedValue(makeProposalPage([], 0)) })
    expect(await screen.findByText("No proposals waiting for review")).toBeInTheDocument()
  })

  it("opens the first proposal when none is in the URL and rewrites the URL", async () => {
    setup()
    expect(await screen.findByText("1 of 3")).toBeInTheDocument()
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("?cut=a"))
    expect(screen.getByAltText("Source image")).toHaveAttribute("src", "http://example.com/api/images/src-a")
    expect(screen.getByAltText("Proposed cut")).toHaveAttribute("src", "http://example.com/api/images/cut-a")
  })

  it("falls back to the first proposal when the id in the URL does not exist", async () => {
    const api = setup({
      getRecutProposal: vi.fn((id: string) => id === "nope"
        ? Promise.reject(new CuratorRequestError(404))
        : Promise.resolve(makeProposalDetail(id))),
    }, "/admin/recut?cut=nope")
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("?cut=a"))
    expect(await screen.findByText("1 of 3")).toBeInTheDocument()
    expect(api.getRecutProposal).toHaveBeenCalledWith("nope")
  })

  it("falls back to the first proposal when the id in the URL is not even a valid id (the server answers 422)", async () => {
    setup({
      getRecutProposal: vi.fn((id: string) => id === "garbage"
        ? Promise.reject(new CuratorRequestError(422))
        : Promise.resolve(makeProposalDetail(id))),
    }, "/admin/recut?cut=garbage")
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("?cut=a"))
    expect(await screen.findByText("1 of 3")).toBeInTheDocument()
  })

  it("says so, with a retry, when the first proposal itself has gone (instead of loading forever)", async () => {
    setup({ getRecutProposal: vi.fn().mockRejectedValue(new CuratorRequestError(404)) })
    expect(await screen.findByText("This proposal is no longer available")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument()
    expect(screen.queryByText("Loading proposal...")).not.toBeInTheDocument()
  })

  it("leaves Alt+arrow (browser back/forward) and other modified arrows alone", async () => {
    setup()
    await screen.findByText("1 of 3")

    fireEvent.keyDown(window, { key: "ArrowRight", altKey: true })
    fireEvent.keyDown(window, { key: "ArrowRight", ctrlKey: true })
    fireEvent.keyDown(window, { key: "ArrowRight", metaKey: true })
    fireEvent.keyDown(window, { key: "ArrowRight", shiftKey: true })

    expect(screen.getByText("1 of 3")).toBeInTheDocument()
    fireEvent.keyDown(window, { key: "ArrowRight" })
    expect(await screen.findByText("2 of 3")).toBeInTheDocument()
  })

  it("steps with the buttons and the arrow keys", async () => {
    const user = userEvent.setup()
    setup()
    await screen.findByText("1 of 3")
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled()

    await user.click(screen.getByRole("button", { name: "Next" }))
    expect(await screen.findByText("2 of 3")).toBeInTheDocument()
    expect(screen.getByTestId("where")).toHaveTextContent("?cut=b")

    await user.keyboard("{ArrowRight}")
    expect(await screen.findByText("3 of 3")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled()

    await user.keyboard("{ArrowLeft}")
    expect(await screen.findByText("2 of 3")).toBeInTheDocument()
  })

  it("opens a proposal from the thumbnail strip", async () => {
    const user = userEvent.setup()
    setup()
    await screen.findByText("1 of 3")
    await user.click(screen.getByRole("button", { name: "Open proposal 3" }))
    expect(await screen.findByText("3 of 3")).toBeInTheDocument()
  })

  it("draws the box only once the source size is known, in percentages", async () => {
    setup({
      getRecutProposal: vi.fn().mockResolvedValue(makeProposalDetail("a", { box: { x: 100, y: 50, w: 400, h: 100 } })),
    })
    const source = await screen.findByAltText("Source image")
    expect(screen.queryByTestId("recut-box")).not.toBeInTheDocument()

    Object.defineProperty(source, "naturalWidth", { value: 1000 })
    Object.defineProperty(source, "naturalHeight", { value: 500 })
    fireEvent.load(source)

    const box = await screen.findByTestId("recut-box")
    expect(box).toHaveStyle({ left: "10%", top: "10%", width: "40%", height: "20%" })
    expect(screen.getByText(/Removes 92% of the source/)).toBeInTheDocument()
  })

  it("shows confidence, box and detector", async () => {
    setup({
      getRecutProposal: vi.fn().mockResolvedValue(makeProposalDetail("a", { confidence: 0.934, detector: "margin_trim", detectorVersion: "3" })),
    })
    expect(await screen.findByText("0.93")).toBeInTheDocument()
    expect(screen.getByText("10, 20, 300 x 200")).toBeInTheDocument()
    expect(screen.getByText("margin_trim 3")).toBeInTheDocument()
  })

  it("shows no confidence for an unscored proposal", async () => {
    setup({ getRecutProposal: vi.fn().mockResolvedValue(makeProposalDetail("a", { confidence: null })) })
    expect(await screen.findByText("not scored")).toBeInTheDocument()
  })

  it("keeps approve and reject visible but disabled", async () => {
    setup()
    await screen.findByText("1 of 3")
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "Reject" })).toBeDisabled()
    expect(screen.getByText(/arrives with the approve\/reject step/)).toBeInTheDocument()
  })

  it("shows a placeholder for an image whose file is missing", async () => {
    setup()
    fireEvent.error(await screen.findByAltText("Proposed cut"))
    expect(await screen.findByText("Cut file missing")).toBeInTheDocument()
    expect(screen.getByAltText("Source image")).toBeInTheDocument()
  })

  it("offers a retry when the list cannot be loaded", async () => {
    const user = userEvent.setup()
    const list = vi.fn().mockRejectedValueOnce(new CuratorRequestError(500)).mockResolvedValue(makeProposalPage(["a"], 1))
    setup({ listRecutProposals: list })
    await user.click(await screen.findByRole("button", { name: "Retry" }))
    expect(await screen.findByText("1 of 1")).toBeInTheDocument()
  })

  it("loads the next page when the open proposal nears the end of what is loaded", async () => {
    const list = vi.fn()
      .mockResolvedValueOnce(makeProposalPage(["a", "b"], 3))
      .mockResolvedValueOnce(makeProposalPage(["c"], 3))
    setup({ listRecutProposals: list })
    await screen.findByText("1 of 3")
    await waitFor(() => expect(list).toHaveBeenCalledTimes(2))
    expect(list).toHaveBeenLastCalledWith(2, 50)
  })
})
