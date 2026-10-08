import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import ChipGroup from "./ChipGroup"

const values = [
  { key: "annoyance", confidence: 0.9, source: "model" as const },
  { key: "joy", confidence: 0.5, source: "model" as const },
]
const options = [{ key: "pride", label: "Pride", definition: "d" }]
const labelOf = (key: string) => key.toUpperCase()

function setup(overrides = {}) {
  const props = { axis: "emotion" as const, label: "Emotion", values, labelOf, options, maxValues: 3, onAdd: vi.fn(), onRemove: vi.fn(), ...overrides }
  render(<ChipGroup {...props} />)
  return props
}

describe("ChipGroup", () => {
  it("renders a labelled list of chips with accessible remove buttons", () => {
    setup()
    expect(screen.getByRole("list", { name: "Emotion" })).toBeInTheDocument()
    expect(screen.getByText("ANNOYANCE")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Remove emotion: ANNOYANCE" })).toBeInTheDocument()
  })

  it("removes a value and moves focus to the next chip's remove button", async () => {
    const user = userEvent.setup()
    const props = setup()
    await user.click(screen.getByRole("button", { name: "Remove emotion: ANNOYANCE" }))
    expect(props.onRemove).toHaveBeenCalledWith("annoyance")
  })

  it("hides the add button when the axis is full", () => {
    setup({ maxValues: 2 })
    expect(screen.queryByRole("button", { name: /add emotion/i })).not.toBeInTheDocument()
  })

  it("only offers values that are not already chosen and adds the picked one", async () => {
    const user = userEvent.setup()
    const props = setup({ options: [{ key: "joy", label: "Joy", definition: "d" }, ...options] })
    await user.click(screen.getByRole("button", { name: /add emotion/i }))
    expect(screen.queryByRole("option", { name: "Joy" })).not.toBeInTheDocument()
    await user.click(screen.getByRole("option", { name: "Pride" }))
    expect(props.onAdd).toHaveBeenCalledWith("pride")
  })

  it("shows an empty axis as such", () => {
    setup({ values: [] })
    expect(screen.getByText("none")).toBeInTheDocument()
  })
})
