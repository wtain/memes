import { render, screen, within } from "@testing-library/react"
import ReviewStats from "./ReviewStats"
import { makeStats } from "../../recut/fixtures"

describe("ReviewStats", () => {
  it("shows a row per band that has proposals, with the approve rate as a percentage", () => {
    render(<ReviewStats stats={makeStats()} />)

    const row = screen.getByRole("row", { name: /0\.9-1\.0/ })
    expect(within(row).getByText("88%")).toBeInTheDocument()
    expect(within(row).getByText("3")).toBeInTheDocument()
  })

  it("shows how many proposals of each band were adjusted, and in total", () => {
    render(<ReviewStats stats={makeStats()} />)

    expect(screen.getByRole("columnheader", { name: "Adjusted" })).toBeInTheDocument()
    const cells = (name: RegExp) => within(screen.getByRole("row", { name })).getAllByRole("cell").map((c) => c.textContent)
    expect(cells(/0\.9-1\.0/)).toEqual(["3", "7", "1", "2", "88%"])
    expect(cells(/total/i)).toEqual(["5", "7", "1", "2", "88%"])
  })

  it("shows a dash instead of a rate while nothing in the band is decided", () => {
    render(<ReviewStats stats={makeStats()} />)

    expect(within(screen.getByRole("row", { name: /0\.8-0\.9/ })).getByText("-")).toBeInTheDocument()
  })

  it("leaves out bands without any proposal", () => {
    render(<ReviewStats stats={makeStats()} />)

    expect(screen.queryByRole("row", { name: /unscored/ })).not.toBeInTheDocument()
  })

  it("shows the total row and explains how the rate differs from the gold numbers", () => {
    render(<ReviewStats stats={makeStats()} />)

    expect(screen.getByRole("row", { name: /total/i })).toBeInTheDocument()
    expect(screen.getByText(/stricter than/i)).toBeInTheDocument()
  })
})
