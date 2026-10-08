import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import MessageBox from "./MessageBox"

function setup(value = "", onSubmit = vi.fn(), onChange = vi.fn()) {
  render(<MessageBox value={value} onChange={onChange} onSubmit={onSubmit} />)
  return { onSubmit, onChange }
}

describe("MessageBox", () => {
  it("has a labelled textarea limited to 600 characters and a counter", () => {
    setup("hello")
    const box = screen.getByLabelText("Message to reply to")
    expect(box).toHaveAttribute("maxlength", "600")
    expect(screen.getByText("5/600")).toBeInTheDocument()
  })

  it("disables Find for blank input and enables it for text", () => {
    const { rerender } = render(<MessageBox value="   " onChange={vi.fn()} onSubmit={vi.fn()} />)
    expect(screen.getByRole("button", { name: "Find a meme" })).toBeDisabled()
    rerender(<MessageBox value="hi" onChange={vi.fn()} onSubmit={vi.fn()} />)
    expect(screen.getByRole("button", { name: "Find a meme" })).toBeEnabled()
  })

  it("submits on Find and on Ctrl+Enter", async () => {
    const user = userEvent.setup()
    const { onSubmit } = setup("hi")
    await user.click(screen.getByRole("button", { name: "Find a meme" }))
    expect(onSubmit).toHaveBeenCalledTimes(1)
    await user.type(screen.getByLabelText("Message to reply to"), "{Control>}{Enter}{/Control}")
    expect(onSubmit).toHaveBeenCalledTimes(2)
  })

  it("reports changes and announces the limit only when it is reached", async () => {
    const user = userEvent.setup()
    const { onChange } = setup("")
    await user.type(screen.getByLabelText("Message to reply to"), "a")
    expect(onChange).toHaveBeenCalledWith("a")
    expect(screen.getByRole("status")).toBeEmptyDOMElement()
  })

  it("shows the limit message at 600 characters", () => {
    setup("x".repeat(600))
    expect(screen.getByRole("status")).toHaveTextContent("Limit of 600 characters reached")
  })

  it("does not submit a blank or whitespace message with Ctrl+Enter", async () => {
    const user = userEvent.setup()
    const { onSubmit } = setup("   ")
    await user.type(screen.getByLabelText("Message to reply to"), "{Control>}{Enter}{/Control}")
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it("shows a visible hint next to the counter when the limit is reached, and not before", () => {
    const { unmount } = render(<MessageBox value="x" onChange={vi.fn()} onSubmit={vi.fn()} />)
    expect(screen.queryByText("Only the first 600 characters are read.")).not.toBeInTheDocument()
    unmount()
    render(<MessageBox value={"x".repeat(600)} onChange={vi.fn()} onSubmit={vi.fn()} />)
    expect(screen.getByText("Only the first 600 characters are read.")).toBeVisible()
  })
})
