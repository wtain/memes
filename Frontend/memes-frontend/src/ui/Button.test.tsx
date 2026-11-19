import { render, screen } from "@testing-library/react"
import { describe, expect, it } from "vitest"
import { Button, ButtonLink, buttonClasses } from "./Button"
import { CheckIcon, ComicsIcon, FlagIcon, TextIcon } from "./icons"

describe("Button", () => {
  it("defaults to type=button and respects an override", () => {
    const { rerender } = render(<Button>Go</Button>)
    expect(screen.getByRole("button", { name: "Go" })).toHaveAttribute("type", "button")
    rerender(<Button type="submit">Go</Button>)
    expect(screen.getByRole("button", { name: "Go" })).toHaveAttribute("type", "submit")
  })

  it.each([
    ["primary", "bg-brand"],
    ["secondary", "ring-line-strong"],
    ["ghost", "hover:bg-black/5"],
    ["danger", "bg-danger"],
  ] as const)("renders the %s variant", (variant, cls) => {
    render(<Button variant={variant}>X</Button>)
    expect(screen.getByRole("button")).toHaveClass(cls)
  })

  it("maps pressed to aria-pressed", () => {
    const { rerender } = render(<Button pressed>X</Button>)
    expect(screen.getByRole("button")).toHaveAttribute("aria-pressed", "true")
    rerender(<Button pressed={false}>X</Button>)
    expect(screen.getByRole("button")).toHaveAttribute("aria-pressed", "false")
    rerender(<Button>X</Button>)
    expect(screen.getByRole("button")).not.toHaveAttribute("aria-pressed")
  })

  it("loading disables and sets aria-busy", () => {
    render(<Button loading>X</Button>)
    const b = screen.getByRole("button")
    expect(b).toBeDisabled()
    expect(b).toHaveAttribute("aria-busy", "true")
  })

  it("appends className last", () => {
    render(<Button className="mt-2">X</Button>)
    expect(screen.getByRole("button").className.endsWith("mt-2")).toBe(true)
  })

  it("sm grows on coarse pointers", () => {
    render(<Button size="sm">X</Button>)
    expect(screen.getByRole("button")).toHaveClass("pointer-coarse:min-h-11")
  })

  it("requires aria-label for icon-only buttons", () => {
    // @ts-expect-error iconOnly without aria-label must not compile
    render(<Button iconOnly>x</Button>)
    render(<Button iconOnly aria-label="Named">x</Button>)
    expect(screen.getByRole("button", { name: "Named" })).toBeInTheDocument()
  })
})

describe("ButtonLink", () => {
  it("renders an anchor", () => {
    render(<ButtonLink href="/x">Open</ButtonLink>)
    expect(screen.getByRole("link", { name: "Open" })).toHaveAttribute("href", "/x")
  })
})

describe("buttonClasses", () => {
  it("builds the class string from variant, size and className", () => {
    const cls = buttonClasses({ variant: "primary", size: "lg", className: "x" })
    expect(cls).toContain("bg-brand")
    expect(cls).toContain("min-h-11")
    expect(cls).toContain("x")
  })

  it("defaults to the same classes as a default ButtonLink", () => {
    render(<ButtonLink href="/x">Open</ButtonLink>)
    expect(buttonClasses()).toBe(screen.getByRole("link").className)
  })
})

describe("icons", () => {
  it("are aria-hidden", () => {
    const { container } = render(<><FlagIcon /><ComicsIcon /><CheckIcon /><TextIcon /></>)
    const svgs = container.querySelectorAll("svg")
    expect(svgs).toHaveLength(4)
    svgs.forEach(s => expect(s).toHaveAttribute("aria-hidden", "true"))
  })
})
