import { StrictMode } from "react"
import { render, screen } from "@testing-library/react"
import { MemoryRouter, Routes, Route, Link } from "react-router-dom"
import { describe, it, expect, vi, beforeEach } from "vitest"
import AppLayout from "./AppLayout"
import { sendPageview } from "../telemetry/pageview"
import userEvent from "@testing-library/user-event"

vi.mock("../telemetry/pageview", () => ({ sendPageview: vi.fn() }))

describe("AppLayout page-visit beacon", () => {
  beforeEach(() => vi.mocked(sendPageview).mockClear())

  it("sends once per navigation, also under StrictMode", async () => {
    render(
      <StrictMode>
        <MemoryRouter initialEntries={["/explore"]}>
          <Routes>
            <Route element={<AppLayout />}>
              <Route path="/explore" element={<Link to="/memes/abc">go</Link>} />
              <Route path="/memes/:id" element={<div>meme</div>} />
            </Route>
          </Routes>
        </MemoryRouter>
      </StrictMode>,
    )
    expect(sendPageview).toHaveBeenCalledTimes(1)
    expect(sendPageview).toHaveBeenLastCalledWith("/explore")
    await userEvent.click(screen.getByText("go"))
    expect(sendPageview).toHaveBeenCalledTimes(2)
    expect(sendPageview).toHaveBeenLastCalledWith("/memes/abc")
  })
})
