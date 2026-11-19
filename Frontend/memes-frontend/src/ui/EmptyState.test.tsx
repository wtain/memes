import { render, screen } from "@testing-library/react"
import { describe, expect, it } from "vitest"
import { Button } from "./Button"
import { EmptyState } from "./EmptyState"

describe("EmptyState", () => {
  it("is a status with the title by default", () => {
    render(<EmptyState title="Nothing to show" />)
    expect(screen.getByRole("status")).toBeInTheDocument()
    expect(screen.getByText("Nothing to show")).toBeInTheDocument()
  })

  it("error tone is an alert", () => {
    render(<EmptyState title="Oops" tone="error" />)
    expect(screen.getByRole("alert")).toBeInTheDocument()
  })

  it("renders description and action only when passed", () => {
    const { rerender } = render(<EmptyState title="T" />)
    expect(screen.queryByText("Details")).toBeNull()
    expect(screen.queryByRole("button")).toBeNull()
    rerender(<EmptyState title="T" description="Details" action={<Button>Retry</Button>} />)
    expect(screen.getByText("Details")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument()
  })

  it("hides the icon from assistive tech", () => {
    render(<EmptyState title="T" icon={<svg data-testid="i" />} />)
    expect(screen.getByTestId("i").parentElement).toHaveAttribute("aria-hidden", "true")
  })
})
