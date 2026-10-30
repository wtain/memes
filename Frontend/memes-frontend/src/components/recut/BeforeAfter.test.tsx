import { fireEvent, render, screen } from "@testing-library/react"
import BeforeAfter from "./BeforeAfter"

const box = { x: 10, y: 20, w: 300, h: 200 }

function loadSource(width = 1000, height = 800) {
  const image = screen.getByAltText("Source image") as HTMLImageElement
  Object.defineProperty(image, "naturalWidth", { value: width, configurable: true })
  Object.defineProperty(image, "naturalHeight", { value: height, configurable: true })
  fireEvent.load(image)
}

describe("BeforeAfter", () => {
  it("draws the static overlay when nothing is being edited", () => {
    render(<BeforeAfter sourceUrl="/s" cutUrl="/c" box={box} />)
    loadSource()

    expect(screen.getByTestId("recut-box")).toBeInTheDocument()
    expect(screen.queryByRole("group", { name: "Crop box editor" })).not.toBeInTheDocument()
    expect(screen.getByText("Removes 93% of the source")).toBeInTheDocument()
  })

  it("shows the editor for the draft instead of the overlay and measures the draft", () => {
    const draft = { x: 0, y: 0, w: 500, h: 400 }
    render(<BeforeAfter sourceUrl="/s" cutUrl="/c" box={box} draft={draft} onDraftChange={() => {}} />)
    loadSource()

    expect(screen.getByRole("group", { name: "Crop box editor" })).toBeInTheDocument()
    expect(screen.queryByTestId("recut-box")).not.toBeInTheDocument()
    expect(screen.getByText("Removes 75% of the source")).toBeInTheDocument()
  })
})
