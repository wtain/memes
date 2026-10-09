import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import RatingBar from "./RatingBar"

describe("RatingBar", () => {
  it("renders a labelled group with three toggle buttons", () => {
    render(<RatingBar grade={null} event={null} onRate={vi.fn()} />)
    const group = screen.getByRole("group", { name: "Rate this result" })
    expect(group).toBeInTheDocument()
    for (const label of ["Good fit", "Acceptable", "Not a fit"]) {
      expect(screen.getByRole("button", { name: label })).toHaveAttribute("aria-pressed", "false")
    }
  })

  it("marks the active grade pressed", () => {
    render(<RatingBar grade={1} event={null} onRate={vi.fn()} />)
    expect(screen.getByRole("button", { name: "Acceptable" })).toHaveAttribute("aria-pressed", "true")
    expect(screen.getByRole("button", { name: "Good fit" })).toHaveAttribute("aria-pressed", "false")
  })

  it("reports the clicked grade, and null when the active one is clicked again", async () => {
    const user = userEvent.setup()
    const onRate = vi.fn()
    const { rerender } = render(<RatingBar grade={null} event={null} onRate={onRate} />)
    await user.click(screen.getByRole("button", { name: "Good fit" }))
    expect(onRate).toHaveBeenLastCalledWith(2)
    rerender(<RatingBar grade={2} event={null} onRate={onRate} />)
    await user.click(screen.getByRole("button", { name: "Good fit" }))
    expect(onRate).toHaveBeenLastCalledWith(null)
    await user.click(screen.getByRole("button", { name: "Not a fit" }))
    expect(onRate).toHaveBeenLastCalledWith(0)
  })

  it("keeps focus on the clicked button", async () => {
    const user = userEvent.setup()
    render(<RatingBar grade={null} event={null} onRate={vi.fn()} />)
    const button = screen.getByRole("button", { name: "Acceptable" })
    await user.click(button)
    expect(button).toHaveFocus()
  })

  it("announces saved and cleared in an always-mounted status element", () => {
    const { rerender } = render(<RatingBar grade={null} event={null} onRate={vi.fn()} />)
    const status = screen.getByRole("status")
    expect(status).toBeEmptyDOMElement()
    rerender(<RatingBar grade={2} event="saved" onRate={vi.fn()} />)
    expect(screen.getByRole("status")).toBe(status)
    expect(status).toHaveTextContent("Saved: Good fit")
    rerender(<RatingBar grade={null} event="cleared" onRate={vi.fn()} />)
    expect(status).toHaveTextContent("Rating cleared")
  })

  it("shows an alert when saving failed", () => {
    render(<RatingBar grade={null} event="failed" onRate={vi.fn()} />)
    expect(screen.getByRole("alert")).toHaveTextContent("Couldn't save the rating")
  })

  it("has touch targets of at least 44 px", () => {
    render(<RatingBar grade={null} event={null} onRate={vi.fn()} />)
    for (const button of screen.getAllByRole("button")) expect(button.className).toContain("min-h-11")
  })
})
