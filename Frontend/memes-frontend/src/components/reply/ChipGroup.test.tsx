import { useState } from "react"
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

  function Stateful({ initial, maxValues = 3, opts = options, removes = true, picker = false }: { initial: string[]; maxValues?: number; opts?: typeof options; removes?: boolean; picker?: boolean }) {
    const [keys, setKeys] = useState(initial)
    return (
      <ChipGroup
        axis="emotion"
        label="Emotion"
        values={keys.map((key) => ({ key, confidence: 1, source: "model" as const }))}
        labelOf={labelOf}
        options={opts}
        maxValues={maxValues}
        pickerDisabled={picker}
        onAdd={(key) => setKeys((k) => [...k, key])}
        onRemove={(key) => removes && setKeys((k) => k.filter((x) => x !== key))}
      />
    )
  }
  const removeBtn = (key: string) => screen.getByRole("button", { name: `Remove emotion: ${key.toUpperCase()}` })

  it("moves focus to the next chip's remove button after removing the first chip", async () => {
    const user = userEvent.setup()
    render(<Stateful initial={["a", "b", "c"]} />)
    await user.click(removeBtn("a"))
    expect(removeBtn("b")).toHaveFocus()
  })

  it("moves focus to the previous chip after removing the last chip", async () => {
    const user = userEvent.setup()
    render(<Stateful initial={["a", "b"]} />)
    await user.click(removeBtn("b"))
    expect(removeBtn("a")).toHaveFocus()
  })

  it("moves focus to the add button after removing the only chip", async () => {
    const user = userEvent.setup()
    render(<Stateful initial={["a"]} />)
    await user.click(removeBtn("a"))
    expect(screen.getByRole("button", { name: /add emotion/i })).toHaveFocus()
  })

  it("keeps focus inside the group when removing from a full axis", async () => {
    const user = userEvent.setup()
    render(<Stateful initial={["a", "b"]} maxValues={2} />)
    expect(screen.queryByRole("button", { name: /add emotion/i })).not.toBeInTheDocument()
    await user.click(removeBtn("a"))
    expect(document.body).not.toHaveFocus()
    expect(removeBtn("b")).toHaveFocus()
  })

  it("does not move focus on a later render when the removal changed nothing", async () => {
    const user = userEvent.setup()
    const { rerender } = render(<Stateful initial={["a", "b"]} removes={false} />)
    await user.click(removeBtn("a"))
    removeBtn("b").focus()
    rerender(<Stateful initial={["a", "b"]} removes={false} />)
    expect(removeBtn("b")).toHaveFocus()
  })

  it("moves focus to the new chip when picking fills the axis", async () => {
    const user = userEvent.setup()
    render(<Stateful initial={["a"]} maxValues={2} opts={[{ key: "pride", label: "Pride", definition: "d" }]} />)
    await user.click(screen.getByRole("button", { name: /add emotion/i }))
    await user.click(screen.getByRole("option", { name: "Pride" }))
    expect(removeBtn("pride")).toHaveFocus()
  })

  it("focuses the list itself when the add button is disabled and no chip is left", async () => {
    const user = userEvent.setup()
    render(<Stateful initial={["a"]} picker />)
    await user.click(removeBtn("a"))
    expect(screen.getByRole("list", { name: "Emotion" })).toHaveFocus()
  })
})
