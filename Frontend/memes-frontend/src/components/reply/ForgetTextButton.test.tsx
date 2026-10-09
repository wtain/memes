import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import ForgetTextButton from "./ForgetTextButton"

describe("ForgetTextButton", () => {
  it("asks for confirmation, explains the effect, and only then calls onConfirm", async () => {
    const user = userEvent.setup()
    const onConfirm = vi.fn().mockResolvedValue(true)
    render(<ForgetTextButton onConfirm={onConfirm} />)

    await user.click(screen.getByRole("button", { name: "Forget this message's text" }))
    expect(onConfirm).not.toHaveBeenCalled()
    expect(screen.getByText(/rating again stores the text again/i)).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "Confirm" }))
    expect(onConfirm).toHaveBeenCalledTimes(1)
    expect(await screen.findByRole("status")).toHaveTextContent("Text forgotten")
  })

  it("can be cancelled", async () => {
    const user = userEvent.setup()
    const onConfirm = vi.fn()
    render(<ForgetTextButton onConfirm={onConfirm} />)
    await user.click(screen.getByRole("button", { name: "Forget this message's text" }))
    await user.click(screen.getByRole("button", { name: "Cancel" }))
    expect(onConfirm).not.toHaveBeenCalled()
    expect(screen.getByRole("button", { name: "Forget this message's text" })).toBeInTheDocument()
  })

  it("reports a failure", async () => {
    const user = userEvent.setup()
    render(<ForgetTextButton onConfirm={vi.fn().mockResolvedValue(false)} />)
    await user.click(screen.getByRole("button", { name: "Forget this message's text" }))
    await user.click(screen.getByRole("button", { name: "Confirm" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't forget the text")
  })
})
