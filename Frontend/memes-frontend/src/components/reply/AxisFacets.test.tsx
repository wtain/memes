import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import AxisFacets from "./AxisFacets"
import { TEST_TAXONOMY } from "../../reply/fixtures"
import { EMPTY_FILTERS } from "../../reply/types"

const facets = [
  { name: "emotion", buckets: [{ value: "joy", count: 2 }, { value: "annoyance", count: 5 }] },
  { name: "tone", buckets: [{ value: "wholesome", count: 1 }] },
]

function setup(selected = EMPTY_FILTERS) {
  const onChange = vi.fn()
  const onClear = vi.fn()
  render(<AxisFacets facets={facets} selected={selected} taxonomy={TEST_TAXONOMY} onChange={onChange} onClear={onClear} />)
  return { onChange, onClear }
}

describe("AxisFacets", () => {
  it("renders a fieldset per axis with labelled checkboxes and counts, most frequent first", () => {
    setup()
    const group = screen.getByRole("group", { name: "Emotion" })
    const boxes = group.querySelectorAll("input[type=checkbox]")
    expect(boxes).toHaveLength(2)
    expect(screen.getByLabelText("Annoyance (5)")).toBeInTheDocument()
    expect(group.textContent?.indexOf("Annoyance")).toBeLessThan(group.textContent?.indexOf("Joy") as number)
  })

  it("reports a selection as the axis's new value list", async () => {
    const user = userEvent.setup()
    const { onChange } = setup({ ...EMPTY_FILTERS, emotion: ["joy"] })
    await user.click(screen.getByLabelText("Annoyance (5)"))
    expect(onChange).toHaveBeenCalledWith("emotion", ["joy", "annoyance"])
  })

  it("unchecks a selected value", async () => {
    const user = userEvent.setup()
    const { onChange } = setup({ ...EMPTY_FILTERS, emotion: ["joy"] })
    expect(screen.getByLabelText("Joy (2)")).toBeChecked()
    await user.click(screen.getByLabelText("Joy (2)"))
    expect(onChange).toHaveBeenCalledWith("emotion", [])
  })

  it("keeps a selected value visible with a zero count when the results no longer contain it", () => {
    setup({ ...EMPTY_FILTERS, tone: ["sarcastic"] })
    expect(screen.getByLabelText("Sarcastic (0)")).toBeChecked()
  })

  it("offers to clear filters only when some are selected", async () => {
    const user = userEvent.setup()
    const { onClear } = setup({ ...EMPTY_FILTERS, tone: ["wholesome"] })
    await user.click(screen.getByRole("button", { name: "Clear filters" }))
    expect(onClear).toHaveBeenCalled()
  })

  it("has no clear button without a selection", () => {
    setup()
    expect(screen.queryByRole("button", { name: "Clear filters" })).not.toBeInTheDocument()
  })
})
