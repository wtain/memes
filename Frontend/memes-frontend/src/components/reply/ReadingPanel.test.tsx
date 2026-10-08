import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import ReadingPanel from "./ReadingPanel"
import { TEST_TAXONOMY, makeProfile } from "../../reply/fixtures"

function setup(overrides = {}) {
  const props = {
    profile: makeProfile(), edited: false, taxonomy: TEST_TAXONOMY,
    onSituation: vi.fn(), onAdd: vi.fn(), onRemove: vi.fn(), onReset: vi.fn(), ...overrides,
  }
  render(<ReadingPanel {...props} />)
  return props
}

describe("ReadingPanel", () => {
  it("shows the language, the situation and a chip group per axis with taxonomy labels", () => {
    setup()
    expect(screen.getByRole("heading", { name: "How I read your message" })).toBeInTheDocument()
    expect(screen.getByLabelText("Situation")).toHaveValue("Manager scheduled a late meeting.")
    expect(screen.getByText("Annoyance")).toBeInTheDocument()
    expect(screen.getByText(/language: en/i)).toBeInTheDocument()
    for (const name of ["Emotion", "Stance", "Topic", "Tone"]) {
      expect(screen.getByRole("list", { name })).toBeInTheDocument()
    }
  })

  it("limits the situation line to 160 characters and reports edits", async () => {
    const user = userEvent.setup()
    const props = setup()
    const input = screen.getByLabelText("Situation")
    expect(input).toHaveAttribute("maxlength", "160")
    await user.type(input, "!")
    expect(props.onSituation).toHaveBeenCalled()
  })

  it("marks an edited reading and offers a reset", async () => {
    const user = userEvent.setup()
    const props = setup({ edited: true })
    expect(screen.getByText("Edited")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Reset to original reading" }))
    expect(props.onReset).toHaveBeenCalled()
  })

  it("does not show the edited marker or reset for an untouched reading", () => {
    setup()
    expect(screen.queryByText("Edited")).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Reset to original reading" })).not.toBeInTheDocument()
  })

  it("explains a degraded empty reading and stays editable", () => {
    setup({ profile: makeProfile({ degraded: true, situation: null, emotion: [], stance: [], topic: [], tone: [] }) })
    expect(screen.getByText(/couldn't read your message structurally/i)).toBeInTheDocument()
    expect(screen.getByLabelText("Situation")).toBeEnabled()
  })

  it("renders chips from their keys while the vocabulary has not loaded, with the picker unavailable", () => {
    setup({ taxonomy: null })
    expect(screen.getByText("annoyance")).toBeInTheDocument()
    expect(screen.getAllByRole("button", { name: /add /i }).every((b) => (b as HTMLButtonElement).disabled)).toBe(true)
  })
})
