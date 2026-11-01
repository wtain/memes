import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import NotFoundButton from "./NotFoundButton"
import { makeMockApi } from "../test/mockApi"

describe("NotFoundButton", () => {
  it("reports the search once and then says it is noted", async () => {
    const user = userEvent.setup()
    const api = makeMockApi()
    render(<NotFoundButton memesApi={api} searchId="s1" />)

    await user.click(screen.getByRole("button", { name: "I didn't find it" }))

    expect(api.reportNotFound).toHaveBeenCalledTimes(1)
    expect(api.reportNotFound).toHaveBeenCalledWith("s1")
    expect(await screen.findByRole("status")).toHaveTextContent("Noted")
    expect(screen.queryByRole("button", { name: "I didn't find it" })).not.toBeInTheDocument()
  })

  it("shows a failure and keeps the button so the press can be retried", async () => {
    const user = userEvent.setup()
    const api = makeMockApi({ reportNotFound: vi.fn().mockRejectedValue(new Error("down")) })
    render(<NotFoundButton memesApi={api} searchId="s1" />)

    await user.click(screen.getByRole("button", { name: "I didn't find it" }))

    expect(await screen.findByRole("status")).toHaveTextContent("Couldn't send, try again")
    expect(screen.getByRole("button", { name: "I didn't find it" })).toBeEnabled()
  })

  it("starts again for a new search", async () => {
    const user = userEvent.setup()
    const api = makeMockApi()
    const { rerender } = render(<NotFoundButton key="s1" memesApi={api} searchId="s1" />)
    await user.click(screen.getByRole("button", { name: "I didn't find it" }))
    expect(await screen.findByRole("status")).toHaveTextContent("Noted")

    rerender(<NotFoundButton key="s2" memesApi={api} searchId="s2" />)

    expect(screen.getByRole("button", { name: "I didn't find it" })).toBeInTheDocument()
  })
})
