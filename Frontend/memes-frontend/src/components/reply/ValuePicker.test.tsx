import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import ValuePicker from "./ValuePicker"

const options = [
  { key: "joy", label: "Joy", definition: "Happiness" },
  { key: "pride", label: "Pride", definition: "Satisfaction" },
]

describe("ValuePicker", () => {
  it("opens a listbox from the button and picks with a click", async () => {
    const user = userEvent.setup()
    const onPick = vi.fn()
    render(<ValuePicker axisLabel="emotion" options={options} onPick={onPick} />)
    const button = screen.getByRole("button", { name: /add emotion/i })
    expect(button).toHaveAttribute("aria-expanded", "false")

    await user.click(button)
    expect(button).toHaveAttribute("aria-expanded", "true")
    await user.click(screen.getByRole("option", { name: "Pride" }))

    expect(onPick).toHaveBeenCalledWith("pride")
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument()
  })

  it("is keyboard operable: arrows move, Enter picks, Escape closes and returns focus", async () => {
    const user = userEvent.setup()
    const onPick = vi.fn()
    render(<ValuePicker axisLabel="emotion" options={options} onPick={onPick} />)
    const button = screen.getByRole("button", { name: /add emotion/i })

    await user.click(button)
    await user.keyboard("{ArrowDown}{Enter}")
    expect(onPick).toHaveBeenCalledWith("pride")

    await user.click(button)
    await user.keyboard("{Escape}")
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument()
    expect(button).toHaveFocus()
  })

  it("shows the definition as a tooltip and an empty message when nothing is left", async () => {
    const user = userEvent.setup()
    const { rerender } = render(<ValuePicker axisLabel="emotion" options={options} onPick={vi.fn()} />)
    await user.click(screen.getByRole("button", { name: /add emotion/i }))
    expect(screen.getByRole("option", { name: "Joy" })).toHaveAttribute("title", "Happiness")

    rerender(<ValuePicker axisLabel="emotion" options={[]} onPick={vi.fn()} />)
    expect(screen.getByText("No more values")).toBeInTheDocument()
  })

  it("can be disabled", () => {
    render(<ValuePicker axisLabel="emotion" options={options} disabled onPick={vi.fn()} />)
    expect(screen.getByRole("button", { name: /add emotion/i })).toBeDisabled()
  })
})
