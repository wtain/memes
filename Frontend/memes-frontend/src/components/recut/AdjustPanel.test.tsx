import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { useState } from "react"
import AdjustPanel from "./AdjustPanel"

const size = { width: 1000, height: 800 }
const current = { x: 10, y: 20, w: 300, h: 200 }

function setup(overrides: Partial<Parameters<typeof AdjustPanel>[0]> = {}) {
  const handlers = { onChange: vi.fn(), onApply: vi.fn(), onCancel: vi.fn(), onReset: vi.fn() }
  render(<AdjustPanel draft={current} size={size} current={current} detectedBox={null} disabled={false}
                      {...handlers} {...overrides} />)
  return handlers
}

describe("AdjustPanel", () => {
  it("shows the draft in four number fields", () => {
    setup({ draft: { x: 1, y: 2, w: 30, h: 40 } })

    expect(screen.getByRole("spinbutton", { name: "x" })).toHaveValue(1)
    expect(screen.getByRole("spinbutton", { name: "y" })).toHaveValue(2)
    expect(screen.getByRole("spinbutton", { name: "w" })).toHaveValue(30)
    expect(screen.getByRole("spinbutton", { name: "h" })).toHaveValue(40)
  })

  // The page owns the draft and feeds every change back, so these tests do the same.
  function Harness({ onChange }: { onChange: (box: typeof current) => void }) {
    const [draft, setDraft] = useState(current)
    return (
      <AdjustPanel draft={draft} size={size} current={current} detectedBox={null} disabled={false}
                   onChange={(box) => { setDraft(box); onChange(box) }} onApply={() => {}} onCancel={() => {}} onReset={() => {}} />
    )
  }

  it("passes a typed value on as soon as it is a whole number", async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(<Harness onChange={onChange} />)

    await user.clear(screen.getByRole("spinbutton", { name: "w" }))
    await user.type(screen.getByRole("spinbutton", { name: "w" }), "250")

    expect(onChange).toHaveBeenLastCalledWith({ ...current, w: 250 })
    expect(screen.getByRole("spinbutton", { name: "w" })).toHaveValue(250)
  })

  it("does not pass on an empty or negative entry", async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(<Harness onChange={onChange} />)

    await user.clear(screen.getByRole("spinbutton", { name: "h" }))
    await user.type(screen.getByRole("spinbutton", { name: "h" }), "-")

    expect(onChange).not.toHaveBeenCalled()
  })

  it("keeps Apply off while a field is half typed", async () => {
    const user = userEvent.setup()
    render(<Harness onChange={() => {}} />)
    await user.clear(screen.getByRole("spinbutton", { name: "w" }))
    await user.type(screen.getByRole("spinbutton", { name: "w" }), "250")
    expect(screen.getByRole("button", { name: "Apply" })).toBeEnabled()

    await user.clear(screen.getByRole("spinbutton", { name: "h" }))

    expect(screen.getByRole("button", { name: "Apply" })).toBeDisabled()
  })

  it.each([
    ["equals the current box", current],
    ["is under 16 px", { ...current, w: 15 }],
    ["leaves the image", { ...current, x: 900 }],
  ])("keeps Apply off while the draft %s", (_name, draft) => {
    setup({ draft })

    expect(screen.getByRole("button", { name: "Apply" })).toBeDisabled()
  })

  it("applies a valid box that differs from the current one", async () => {
    const user = userEvent.setup()
    const { onApply } = setup({ draft: { ...current, w: 250 } })

    await user.click(screen.getByRole("button", { name: "Apply" }))

    expect(onApply).toHaveBeenCalledTimes(1)
  })

  it("disables everything while saving", () => {
    setup({ draft: { ...current, w: 250 }, disabled: true })

    expect(screen.getByRole("button", { name: "Apply" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled()
    expect(screen.getByRole("spinbutton", { name: "x" })).toBeDisabled()
  })

  it("cancels", async () => {
    const user = userEvent.setup()
    const { onCancel } = setup()

    await user.click(screen.getByRole("button", { name: "Cancel" }))

    expect(onCancel).toHaveBeenCalledTimes(1)
  })

  it("offers to reset to the detector's box only when there is one and it differs from the current box", async () => {
    const user = userEvent.setup()
    const { onReset } = setup({ detectedBox: { x: 0, y: 0, w: 900, h: 700 } })

    await user.click(screen.getByRole("button", { name: "Reset to detected box" }))

    expect(onReset).toHaveBeenCalledTimes(1)
  })

  it("has no reset button without a stored box or when the box already is the detector's", () => {
    const { unmount } = render(
      <AdjustPanel draft={current} size={size} current={current} detectedBox={null} disabled={false}
                   onChange={() => {}} onApply={() => {}} onCancel={() => {}} onReset={() => {}} />)
    expect(screen.queryByRole("button", { name: "Reset to detected box" })).not.toBeInTheDocument()
    unmount()

    setup({ detectedBox: current })
    expect(screen.queryByRole("button", { name: "Reset to detected box" })).not.toBeInTheDocument()
  })
})
