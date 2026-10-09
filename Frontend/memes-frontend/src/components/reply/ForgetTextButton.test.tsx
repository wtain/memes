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

  it("announces through one persistent status element", async () => {
    const user = userEvent.setup()
    render(<ForgetTextButton onConfirm={vi.fn().mockResolvedValue(true)} />)
    const status = screen.getByRole("status")
    expect(status).toBeEmptyDOMElement()
    await user.click(screen.getByRole("button", { name: "Forget this message's text" }))
    expect(screen.getByRole("status")).toBe(status)
    await user.click(screen.getByRole("button", { name: "Confirm" }))
    expect(screen.getByRole("status")).toBe(status)
    expect(status).toHaveTextContent("Text forgotten")
  })

  it("returns focus to the forget button after done, failure and cancel", async () => {
    const user = userEvent.setup()
    const onConfirm = vi.fn().mockResolvedValueOnce(true).mockResolvedValueOnce(false)
    render(<ForgetTextButton onConfirm={onConfirm} />)
    const forget = () => screen.getByRole("button", { name: "Forget this message's text" })

    await user.click(forget())
    await user.click(screen.getByRole("button", { name: "Confirm" }))
    await screen.findByText("Text forgotten", { selector: "[aria-hidden]" })
    expect(forget()).toHaveFocus()                                // done

    await user.click(forget())
    await user.click(screen.getByRole("button", { name: "Confirm" }))
    expect(await screen.findByRole("alert")).toBeInTheDocument()
    expect(forget()).toHaveFocus()                                // failed

    await user.click(forget())
    await user.click(screen.getByRole("button", { name: "Cancel" }))
    expect(forget()).toHaveFocus()                                // cancelled
  })

  it("disables Cancel while the request is running", async () => {
    const user = userEvent.setup()
    let finish!: (ok: boolean) => void
    render(<ForgetTextButton onConfirm={() => new Promise<boolean>((resolve) => { finish = resolve })} />)
    await user.click(screen.getByRole("button", { name: "Forget this message's text" }))
    await user.click(screen.getByRole("button", { name: "Confirm" }))
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled()
    finish(true)
    expect(await screen.findByText("Text forgotten", { selector: "[aria-hidden]" })).toBeInTheDocument()
  })
})
