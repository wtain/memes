import { render, screen } from "@testing-library/react"
import ProposalInfo from "./ProposalInfo"
import { makeProposalDetail } from "../../recut/fixtures"

describe("ProposalInfo", () => {
  it("shows the detector's box and says what the confidence refers to once the proposal was adjusted", () => {
    render(<ProposalInfo detail={makeProposalDetail("a", { detectedBox: { x: 0, y: 0, w: 900, h: 700 } })} />)

    expect(screen.getByText("Adjusted")).toBeInTheDocument()
    expect(screen.getByText("0, 0, 900 x 700")).toBeInTheDocument()
    expect(screen.getByText(/confidence refers to the detector's original box/i)).toBeInTheDocument()
  })

  it("adds nothing for a proposal that was never adjusted", () => {
    render(<ProposalInfo detail={makeProposalDetail("a")} />)

    expect(screen.queryByText("Adjusted")).not.toBeInTheDocument()
    expect(screen.queryByText(/original box/i)).not.toBeInTheDocument()
  })
})
