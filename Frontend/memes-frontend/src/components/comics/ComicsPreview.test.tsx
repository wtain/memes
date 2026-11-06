import { fireEvent, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, describe, expect, it, vi } from "vitest"
import ComicsPreview from "./ComicsPreview"

afterEach(() => vi.unstubAllGlobals())

describe("ComicsPreview", () => {
  it("shows a Retry button when the preview fails and repeats the request", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 500, json: async () => ({}) }))
    render(<ComicsPreview url="http://x/preview?v=1" />)
    fireEvent.error(screen.getByAltText("Comics preview"))
    expect(await screen.findByRole("alert")).toBeInTheDocument()
    await userEvent.click(screen.getByRole("button", { name: "Retry" }))
    const img = await screen.findByAltText("Comics preview")
    expect(img).toHaveAttribute("src", "http://x/preview?v=1")
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
  })
})
