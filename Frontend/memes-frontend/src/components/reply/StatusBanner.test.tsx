import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import StatusBanner from "./StatusBanner"

describe("StatusBanner", () => {
  it("keeps an always-mounted, empty polite live region when idle, ready or refining", () => {
    for (const status of ["idle", "ready", "refining"] as const) {
      const { unmount } = render(<StatusBanner status={status} fallback="none" error={null} onRetry={vi.fn()} />)
      const region = screen.getByRole("status")
      expect(region).toHaveAttribute("aria-live", "polite")
      expect(region).toBeEmptyDOMElement()
      expect(screen.queryByRole("alert")).not.toBeInTheDocument()
      unmount()
    }
  })

  it("fills the same live region element when the message changes, instead of mounting a new one", () => {
    const { rerender } = render(<StatusBanner status="idle" fallback="none" error={null} onRetry={vi.fn()} />)
    const region = screen.getByRole("status")
    rerender(<StatusBanner status="reading" fallback="none" error={null} onRetry={vi.fn()} />)
    expect(screen.getByRole("status")).toBe(region)
    expect(region).toHaveTextContent("Reading your message")
  })

  it("announces the first reading", () => {
    render(<StatusBanner status="reading" fallback="none" error={null} onRetry={vi.fn()} />)
    expect(screen.getByRole("status")).toHaveTextContent("Reading your message")
  })

  it("explains a degraded search from the fallback reasons", () => {
    render(<StatusBanner status="degraded" fallback="timeout+embed_unavailable" error={null} onRetry={vi.fn()} />)
    const banner = screen.getByRole("status")
    expect(banner).toHaveTextContent("Limited understanding")
    expect(banner).toHaveTextContent(/took too long/i)
    expect(banner).toHaveTextContent(/similarity model/i)
  })

  it("says message search is not available in this environment", () => {
    render(<StatusBanner status="unavailable" fallback="unavailable" error={null} onRetry={vi.fn()} />)
    expect(screen.getByRole("status")).toHaveTextContent("Message search isn't available in this environment")
  })

  it("shows an alert with a retry button for errors, without any message text", async () => {
    const user = userEvent.setup()
    const onRetry = vi.fn()
    render(<StatusBanner status="error" fallback="none" error="validation" onRetry={onRetry} />)
    expect(screen.getByRole("alert")).toHaveTextContent(/rejected/i)
    await user.click(screen.getByRole("button", { name: "Try again" }))
    expect(onRetry).toHaveBeenCalled()
  })

  it("uses different wording for a server failure", () => {
    render(<StatusBanner status="error" fallback="none" error="failed" onRetry={vi.fn()} />)
    expect(screen.getByRole("alert")).toHaveTextContent(/something went wrong/i)
  })

  it("renders a degraded banner without reasons for unknown, empty or prototype tokens", () => {
    for (const fallback of ["bogus", "", "none", "constructor", "toString+__proto__"]) {
      const { unmount } = render(<StatusBanner status="degraded" fallback={fallback} error={null} onRetry={vi.fn()} />)
      const banner = screen.getByRole("status")
      expect(banner).toHaveTextContent("Limited understanding. A simpler match was used. Results may be less precise.")
      expect(banner).not.toHaveTextContent(/function|object/i)
      unmount()
    }
  })
})
