import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { describe, expect, it } from "vitest"
import { makeMockApi } from "../test/mockApi"
import type { Meme } from "../types/generated/all"
import { Rail } from "./Rail"
import type { Feed } from "./useHomeFeeds"

const memes = (n: number): Meme[] =>
  Array.from({ length: n }, (_, i) => ({ id: `m${i}`, imageUrl: `/i/${i}`, text: [], tags: [], flagged: false }))

function setup(feed: Feed, extra: Partial<React.ComponentProps<typeof Rail>> = {}) {
  const onOpenMeme = vi.fn()
  const onRetry = vi.fn()
  const view = render(
    <MemoryRouter>
      <Rail
        title="Recently added"
        seeAllTo="/explore"
        feed={feed}
        memesApi={makeMockApi()}
        onOpenMeme={onOpenMeme}
        onRetry={onRetry}
        errorMessage="Could not load recent memes."
        {...extra}
      />
    </MemoryRouter>,
  )
  return { ...view, onOpenMeme, onRetry }
}

describe("Rail", () => {
  it("renders the heading, twelve cards, a See all link and the subtitle", () => {
    setup({ status: "ready", items: memes(12) }, { subtitle: "Memes that keep coming back." })
    expect(screen.getByRole("heading", { level: 2, name: "Recently added" })).toBeInTheDocument()
    expect(screen.getAllByTestId("card-root")).toHaveLength(12)
    expect(screen.getByRole("link", { name: /see all/i })).toHaveAttribute("href", "/explore")
    expect(screen.getByText("Memes that keep coming back.")).toBeInTheDocument()
  })

  it("shows six skeleton cards and marks the section busy while loading", () => {
    setup({ status: "loading", items: [] })
    expect(screen.getAllByTestId("meme-card-skeleton")).toHaveLength(6)
    expect(screen.getByRole("region", { name: "Recently added" })).toHaveAttribute("aria-busy", "true")
  })

  it("shows the error with a working Try again and keeps the heading", async () => {
    const { onRetry } = setup({ status: "error", items: [] })
    expect(screen.getByRole("heading", { level: 2, name: "Recently added" })).toBeInTheDocument()
    expect(within(screen.getByRole("alert")).getByText("Could not load recent memes.")).toBeInTheDocument()
    await userEvent.click(screen.getByRole("button", { name: "Try again" }))
    expect(onRetry).toHaveBeenCalledTimes(1)
  })

  it("renders nothing when ready with no items", () => {
    const { container } = setup({ status: "ready", items: [] })
    expect(container).toBeEmptyDOMElement()
  })

  it("gives cards a fixed width and the scroller snap scrolling", () => {
    setup({ status: "ready", items: memes(2) })
    const wrapper = screen.getAllByTestId("card-root")[0].parentElement as HTMLElement
    expect(wrapper).toHaveClass("w-40", "sm:w-44", "shrink-0", "snap-start")
    expect(wrapper.parentElement).toHaveClass("overflow-x-auto", "snap-x")
  })

  it("opens a meme through the callback without a full page load", async () => {
    const { onOpenMeme } = setup({ status: "ready", items: memes(2) })
    const link = screen.getAllByRole("link", { name: /meme/i })[1]
    const notPrevented = fireClick(link)
    expect(notPrevented).toBe(false)
    expect(onOpenMeme).toHaveBeenCalledWith("m1")
  })
})

function fireClick(el: HTMLElement): boolean {
  const event = new MouseEvent("click", { bubbles: true, cancelable: true, button: 0 })
  return el.dispatchEvent(event)
}
