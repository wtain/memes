import { render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { describe, expect, it } from "vitest"
import { FeaturesContext } from "../features/useFeature"
import type { CapabilitiesFeatures } from "../types/generated/all"
import { ReplyShortcut } from "./ReplyShortcut"

const ALL_ON: CapabilitiesFeatures = {
  memeFromText: true, concepts: true, trends: true, comics: true, ratings: true,
  statistics: true, uploads: true, history: true, admin: true,
}

function renderShortcut(value?: React.ComponentProps<typeof FeaturesContext.Provider>["value"]) {
  const ui = (
    <MemoryRouter>
      <ReplyShortcut />
    </MemoryRouter>
  )
  return render(value ? <FeaturesContext.Provider value={value}>{ui}</FeaturesContext.Provider> : ui)
}

describe("ReplyShortcut", () => {
  it("shows the card with a link to /reply on dev", () => {
    renderShortcut()
    expect(screen.getByText("Reply with a meme")).toBeInTheDocument()
    expect(screen.getByText("Paste a message you want to answer and get matching memes.")).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "Open Reply" })).toHaveAttribute("href", "/reply")
  })

  it("renders nothing on a prod build", () => {
    const { container } = renderShortcut({ stage: "prod", capabilities: null })
    expect(container).toBeEmptyDOMElement()
  })

  it("still renders nothing on prod when the capability is on (build flag wins)", () => {
    const { container } = renderShortcut({ stage: "prod", capabilities: ALL_ON })
    expect(container).toBeEmptyDOMElement()
  })
})
