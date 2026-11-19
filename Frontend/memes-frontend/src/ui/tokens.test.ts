import { readFileSync } from "node:fs"
import { resolve } from "node:path"
import { describe, expect, it } from "vitest"

const css = readFileSync(resolve(process.cwd(), "src/index.css"), "utf8")

function block(start: string): string {
  const i = css.indexOf(start)
  expect(i).toBeGreaterThanOrEqual(0)
  const open = css.indexOf("{", i)
  let depth = 0
  for (let j = open; j < css.length; j++) {
    if (css[j] === "{") depth++
    if (css[j] === "}") depth--
    if (depth === 0) return css.slice(open + 1, j)
  }
  throw new Error("unbalanced block " + start)
}

const names = (text: string, prefix: string) =>
  [...text.matchAll(new RegExp(`(--${prefix}-[a-z0-9-]+)\\s*:`, "g"))].map(m => m[1])

describe("design tokens", () => {
  it("defines every light token in the dark block", () => {
    const light = names(block(":root {"), "c")
    const dark = new Set(names(block(':root[data-theme="dark"] {'), "c"))
    const missing = light.filter(n => !dark.has(n))
    expect(missing.filter(n => n !== "--c-scrim")).toEqual([])
  })

  it("maps every --color-* to an existing --c-*", () => {
    const root = new Set(names(block(":root {"), "c"))
    const theme = block("@theme inline")
    const refs = [...theme.matchAll(/--color-[a-z0-9-]+\s*:\s*var\((--c-[a-z0-9-]+)\)/g)].map(m => m[1])
    expect(refs.length).toBeGreaterThan(10)
    expect(refs.filter(r => !root.has(r))).toEqual([])
  })

  it("declares the global focus ring", () => {
    expect(css).toContain(":focus-visible")
    expect(css).toContain("var(--c-focus)")
  })
})
