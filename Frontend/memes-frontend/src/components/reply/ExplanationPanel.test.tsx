import { render, screen } from "@testing-library/react"
import ExplanationPanel from "./ExplanationPanel"
import { makeResult } from "../../reply/fixtures"

const labelOf = (axis: string, key: string) => `${axis}:${key}`

describe("ExplanationPanel", () => {
  it("lists each axis with a text percentage and the matched value in the meme's words", () => {
    render(<ExplanationPanel result={makeResult("a")} labelOf={labelOf} />)
    expect(screen.getByText("Situation")).toBeInTheDocument()
    expect(screen.getByText("90%")).toBeInTheDocument()
    expect(screen.getByText(/emotion:resignation/)).toBeInTheDocument()
    expect(screen.getByText(/close to your emotion:annoyance/)).toBeInTheDocument()
  })

  it("shows an exact match plainly", () => {
    const result = makeResult("a")
    result.explanation.axes[1].matched = [{ queryKey: "annoyance", memeKey: "annoyance", similarity: 1, memeConfidence: 1 }]
    render(<ExplanationPanel result={result} labelOf={labelOf} />)
    expect(screen.getByText(/emotion:annoyance/)).toBeInTheDocument()
    expect(screen.queryByText(/close to your/)).not.toBeInTheDocument()
  })

  it("marks a neutral axis", () => {
    const result = makeResult("a")
    result.explanation.axes[1] = { ...result.explanation.axes[1], neutral: true, matched: [], score: 0.3 }
    render(<ExplanationPanel result={result} labelOf={labelOf} />)
    expect(screen.getByText(/no value on this axis/i)).toBeInTheDocument()
  })

  it("names the word-match axis and shows the meme's situation and use-when text", () => {
    const result = makeResult("a")
    result.explanation.axes.push({ axis: "lexical", score: 1, weight: 0.1, contribution: 0.1, neutral: false, queryValues: [], matched: [] })
    render(<ExplanationPanel result={result} labelOf={labelOf} />)
    expect(screen.getByText("Word match")).toBeInTheDocument()
    expect(screen.getByText("A worker accepts another late meeting.")).toBeInTheDocument()
    expect(screen.getByText("Use when plans change at the last minute.")).toBeInTheDocument()
  })
})
