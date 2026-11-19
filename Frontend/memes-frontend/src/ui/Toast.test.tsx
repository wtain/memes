import { StrictMode } from "react"
import { act, fireEvent, render, renderHook, screen, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { ToastProvider } from "./Toast"
import { useToast } from "./toastContext"

function Trigger({ message, tone }: { message: string; tone?: "success" | "info" | "error" }) {
  const toast = useToast()
  return <button onClick={() => toast.show(message, tone)}>go {message}</button>
}

beforeEach(() => {
  vi.useFakeTimers()
})
afterEach(() => {
  vi.useRealTimers()
})

function setup(children: React.ReactNode) {
  return render(<ToastProvider>{children}</ToastProvider>)
}

describe("ToastProvider", () => {
  it("pre-mounts empty polite status and alert regions", () => {
    setup(null)
    const status = screen.getByRole("status")
    const alert = screen.getByRole("alert")
    expect(status.getAttribute("aria-live")).toBe("polite")
    expect(status.textContent).toBe("")
    expect(alert.textContent).toBe("")
  })

  it("shows success in the status region", () => {
    setup(<Trigger message="Image copied" />)
    fireEvent.click(screen.getByText("go Image copied"))
    expect(within(screen.getByRole("status")).getByText("Image copied")).toBeTruthy()
  })

  it("shows errors in the alert region with a dismiss button", () => {
    setup(<Trigger message="Boom" tone="error" />)
    fireEvent.click(screen.getByText("go Boom"))
    const alert = screen.getByRole("alert")
    expect(within(alert).getByText("Boom")).toBeTruthy()
    fireEvent.click(within(alert).getByRole("button", { name: /dismiss/i }))
    expect(alert.textContent).toBe("")
  })

  it("auto-dismisses success after 2.5 s and error after 6 s", () => {
    setup(
      <>
        <Trigger message="ok" />
        <Trigger message="bad" tone="error" />
      </>
    )
    fireEvent.click(screen.getByText("go ok"))
    fireEvent.click(screen.getByText("go bad"))
    act(() => void vi.advanceTimersByTime(2400))
    expect(screen.queryByText("ok")).not.toBeNull()
    act(() => void vi.advanceTimersByTime(200))
    expect(screen.queryByText("ok")).toBeNull()
    expect(screen.queryByText("bad")).not.toBeNull()
    act(() => void vi.advanceTimersByTime(3300))
    expect(screen.queryByText("bad")).not.toBeNull()
    act(() => void vi.advanceTimersByTime(200))
    expect(screen.queryByText("bad")).toBeNull()
  })

  it("pauses on hover and resumes on leave", () => {
    setup(<Trigger message="ok" />)
    fireEvent.click(screen.getByText("go ok"))
    const toast = screen.getByText("ok").closest("[data-toast]") as HTMLElement
    act(() => void vi.advanceTimersByTime(1000))
    fireEvent.mouseEnter(toast)
    act(() => void vi.advanceTimersByTime(10_000))
    expect(screen.queryByText("ok")).not.toBeNull()
    fireEvent.mouseLeave(toast)
    act(() => void vi.advanceTimersByTime(1400))
    expect(screen.queryByText("ok")).not.toBeNull()
    act(() => void vi.advanceTimersByTime(200))
    expect(screen.queryByText("ok")).toBeNull()
  })

  it("keeps at most 3 toasts, dropping the oldest", () => {
    setup(
      <>
        {["a", "b", "c", "d"].map((m) => (
          <Trigger key={m} message={m} />
        ))}
      </>
    )
    for (const m of ["a", "b", "c", "d"]) fireEvent.click(screen.getByText(`go ${m}`))
    const status = screen.getByRole("status")
    expect(within(status).queryByText("a")).toBeNull()
    for (const m of ["b", "c", "d"]) expect(within(status).queryByText(m)).not.toBeNull()
  })

  it("clears timers on unmount", () => {
    const { unmount } = setup(<Trigger message="ok" />)
    fireEvent.click(screen.getByText("go ok"))
    unmount()
    expect(vi.getTimerCount()).toBe(0)
  })

  it("renders one toast under StrictMode", () => {
    render(
      <StrictMode>
        <ToastProvider>
          <Trigger message="once" />
        </ToastProvider>
      </StrictMode>
    )
    fireEvent.click(screen.getByText("go once"))
    expect(screen.getAllByText("once")).toHaveLength(1)
  })
})

describe("useToast without a provider", () => {
  it("returns a show that does not throw", () => {
    const { result } = renderHook(() => useToast())
    expect(() => result.current.show("x")).not.toThrow()
  })
})
