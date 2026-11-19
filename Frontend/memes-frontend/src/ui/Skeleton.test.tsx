import { render, screen } from "@testing-library/react"
import { describe, expect, it } from "vitest"
import { MemeCardSkeleton, Skeleton } from "./Skeleton"

describe("Skeleton", () => {
  it("is hidden from assistive tech and animated only when motion is safe", () => {
    render(<Skeleton data-testid="s" className="h-4" />)
    const el = screen.getByTestId("s")
    expect(el).toHaveAttribute("aria-hidden", "true")
    expect(el).toHaveClass("rounded-md", "bg-line", "motion-safe:animate-pulse")
    expect(el.className.endsWith("h-4")).toBe(true)
  })
})

describe("MemeCardSkeleton", () => {
  it("has a square image block and a footer matching the card footer height", () => {
    const { container } = render(<MemeCardSkeleton />)
    expect(container.firstElementChild).toHaveAttribute("aria-hidden", "true")
    expect(container.querySelector(".aspect-square")).not.toBeNull()
    expect(container.querySelector(".h-10")).not.toBeNull()
    expect(screen.queryByRole("status")).toBeNull()
  })
})
