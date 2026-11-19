import { render, screen } from "@testing-library/react"
import { describe, expect, it } from "vitest"
import { Badge } from "./Badge"

describe("Badge", () => {
  it("renders children with base classes and neutral tone by default", () => {
    render(<Badge>Hi</Badge>)
    const el = screen.getByText("Hi")
    expect(el).toHaveClass("text-meta", "rounded-full", "bg-line")
    expect(el).not.toHaveAttribute("tabindex")
  })

  it.each([
    ["success", "bg-success-bg", "text-success-fg"],
    ["warning", "bg-warning-bg", "text-warning-fg"],
    ["danger", "bg-danger-bg", "text-danger-fg"],
  ] as const)("%s tone", (tone, bg, fg) => {
    render(<Badge tone={tone}>T</Badge>)
    expect(screen.getByText("T")).toHaveClass(bg, fg)
  })

  it("brand tone has a brand tint", () => {
    render(<Badge tone="brand">B</Badge>)
    expect(screen.getByText("B").className).toMatch(/brand/)
  })

  it("passes title and className through", () => {
    render(<Badge title="tip" className="ml-1">X</Badge>)
    const el = screen.getByText("X")
    expect(el).toHaveAttribute("title", "tip")
    expect(el.className.endsWith("ml-1")).toBe(true)
  })
})
