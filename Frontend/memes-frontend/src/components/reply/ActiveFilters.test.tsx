import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import ActiveFilters from "./ActiveFilters"
import { TEST_TAXONOMY } from "../../reply/fixtures"
import { EMPTY_FILTERS } from "../../reply/types"

describe("ActiveFilters", () => {
  it("renders nothing without filters", () => {
    const { container } = render(<ActiveFilters filters={EMPTY_FILTERS} taxonomy={TEST_TAXONOMY} onRemove={vi.fn()} />)
    expect(container).toBeEmptyDOMElement()
  })

  it("lists each filter with an accessible remove button", async () => {
    const user = userEvent.setup()
    const onRemove = vi.fn()
    render(<ActiveFilters filters={{ ...EMPTY_FILTERS, emotion: ["joy"], tone: ["sarcastic"] }} taxonomy={TEST_TAXONOMY} onRemove={onRemove} />)
    await user.click(screen.getByRole("button", { name: "Remove filter emotion: Joy" }))
    expect(onRemove).toHaveBeenCalledWith("emotion", "joy")
    expect(screen.getByRole("button", { name: "Remove filter tone: Sarcastic" })).toBeInTheDocument()
  })
})
