import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import ResultCard from "./ResultCard"
import { makeMockApi } from "../../test/mockApi"
import { makeResult } from "../../reply/fixtures"

const labelOf = (_axis: string, key: string) => key

function setup(result = makeResult("a"), onOpen = vi.fn()) {
  render(<ResultCard result={result} memesApi={makeMockApi()} labelOf={labelOf} onOpen={onOpen} />)
  return { onOpen }
}

describe("ResultCard", () => {
  it("toggles the explanation with an aria-expanded button", async () => {
    const user = userEvent.setup()
    setup()
    const toggle = screen.getByRole("button", { name: "Why this meme" })
    expect(toggle).toHaveAttribute("aria-expanded", "false")
    expect(screen.queryByText("Use when plans change at the last minute.")).not.toBeInTheDocument()

    await user.click(toggle)

    expect(toggle).toHaveAttribute("aria-expanded", "true")
    expect(screen.getByText("Use when plans change at the last minute.")).toBeInTheDocument()
  })

  it("shows a similar-memes badge only when duplicates were collapsed into the card", () => {
    setup(makeResult("a", { clusterSize: 3 }))
    expect(screen.getByText("+2 similar")).toBeInTheDocument()
  })

  it("has no badge for a single or absent cluster size", () => {
    setup(makeResult("a", { clusterSize: 1 }))
    expect(screen.queryByText(/similar/)).not.toBeInTheDocument()
  })

  it("says so when a result matched by words only", () => {
    setup(makeResult("a", { axisPath: false }))
    expect(screen.getByText("Matched by words only")).toBeInTheDocument()
  })

  it("opens the details when the image is clicked", async () => {
    const user = userEvent.setup()
    const { onOpen } = setup()
    await user.click(screen.getByRole("img", { name: "a" }))
    expect(onOpen).toHaveBeenCalled()
  })

  it("shows no words-only note when the axis path matched", () => {
    setup(makeResult("a", { axisPath: true }))
    expect(screen.queryByText("Matched by words only")).not.toBeInTheDocument()
  })

  it("points aria-controls at the opened panel", async () => {
    const user = userEvent.setup()
    setup()
    const toggle = screen.getByRole("button", { name: "Why this meme" })
    await user.click(toggle)
    const panel = screen.getByText("Use when plans change at the last minute.").closest("[id]")
    expect(panel).not.toBeNull()
    expect(toggle.getAttribute("aria-controls")).toBe(panel!.id)
  })
})
