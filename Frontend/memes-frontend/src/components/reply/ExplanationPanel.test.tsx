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

  it("clamps out-of-range and non-finite scores", () => {
    const result = makeResult("a")
    result.explanation.axes[0] = { ...result.explanation.axes[0], score: 1.2 }
    result.explanation.axes[1] = { ...result.explanation.axes[1], score: -0.5 }
    result.explanation.axes.push({ axis: "lexical", score: Number.NaN, weight: 0.1, contribution: 0, neutral: false, queryValues: [], matched: [] })
    render(<ExplanationPanel result={result} labelOf={labelOf} />)
    expect(screen.getByText("100%")).toBeInTheDocument()
    expect(screen.getAllByText("0%")).toHaveLength(2)
    expect(screen.queryByText(/NaN/)).not.toBeInTheDocument()
    const bars = screen.getAllByTestId("score-bar")
    expect((bars[0].firstElementChild as HTMLElement).style.width).toBe("100%")
    expect((bars[1].firstElementChild as HTMLElement).style.width).toBe("0%")
    expect((bars[2].firstElementChild as HTMLElement).style.width).toBe("0%")
  })

  it("reads each score as a labelled value and hides the decorative bar", () => {
    render(<ExplanationPanel result={makeResult("a")} labelOf={labelOf} />)
    expect(screen.getAllByText("score", { exact: false, selector: ".sr-only" })[0]).toBeInTheDocument()
    for (const bar of screen.getAllByTestId("score-bar")) expect(bar).toHaveAttribute("aria-hidden", "true")
  })
})
