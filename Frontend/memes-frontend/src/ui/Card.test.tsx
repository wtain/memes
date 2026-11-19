import { createRef } from "react"
import { fireEvent, render, screen } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"
import { Card } from "./Card"

describe("Card", () => {
  it("has base classes only by default", () => {
    render(<Card data-testid="c" />)
    const el = screen.getByTestId("c")
    expect(el).toHaveClass("rounded-lg", "bg-surface", "shadow-card", "ring-1", "ring-line", "overflow-hidden")
    expect(el).not.toHaveClass("p-4")
    expect(el.className).not.toMatch(/hover:shadow-card-hover/)
    expect(el.className).not.toMatch(/border-black/)
  })

  it("padded and interactive add classes", () => {
    render(<Card data-testid="c" padded interactive />)
    expect(screen.getByTestId("c")).toHaveClass("p-4", "hover:shadow-card-hover", "motion-safe:transition-shadow")
  })

  it("passes props, ref and className through", () => {
    const ref = createRef<HTMLDivElement>()
    const onClick = vi.fn()
    render(<Card data-testid="c" ref={ref} onClick={onClick} className="mt-2" />)
    fireEvent.click(screen.getByTestId("c"))
    expect(onClick).toHaveBeenCalled()
    expect(ref.current).toBe(screen.getByTestId("c"))
    expect(screen.getByTestId("c").className.endsWith("mt-2")).toBe(true)
  })
})
