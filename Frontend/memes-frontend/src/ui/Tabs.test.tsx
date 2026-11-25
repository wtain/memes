import { fireEvent, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"
import { useState } from "react"
import { Tabs } from "./Tabs"
import { panelId, tabId } from "./tabIds"

const TABS = [
  { id: "info", label: "Info" },
  { id: "similar", label: "Similar" },
  { id: "stats", label: "Stats" },
]

function setup(initial = "info") {
  const onChange = vi.fn()
  function Host() {
    const [value, setValue] = useState(initial)
    return (
      <Tabs
        tabs={TABS}
        value={value}
        onChange={id => {
          onChange(id)
          setValue(id)
        }}
        idPrefix="m1"
        aria-label="Meme info"
      />
    )
  }
  render(<Host />)
  return onChange
}

describe("Tabs", () => {
  it("renders a tablist with one tab per entry and marks the selected one", () => {
    setup("similar")
    expect(screen.getByRole("tablist", { name: "Meme info" })).toBeInTheDocument()
    const tabs = screen.getAllByRole("tab")
    expect(tabs.map(t => t.textContent)).toEqual(["Info", "Similar", "Stats"])
    expect(tabs.map(t => t.getAttribute("aria-selected"))).toEqual(["false", "true", "false"])
    expect(tabs[0]).toHaveAttribute("id", tabId("m1", "info"))
    expect(tabs[0]).toHaveAttribute("aria-controls", panelId("m1", "info"))
    expect(tabs[1]).toHaveAttribute("aria-controls", panelId("m1", "similar"))
    expect(tabId("m1", "info")).not.toBe(panelId("m1", "info"))
  })

  it("only the selected tab is tabbable", () => {
    setup("similar")
    expect(screen.getAllByRole("tab").map(t => t.getAttribute("tabindex"))).toEqual(["-1", "0", "-1"])
  })

  it("tab buttons are type=button", () => {
    setup()
    for (const t of screen.getAllByRole("tab")) expect(t).toHaveAttribute("type", "button")
  })

  it("click selects", async () => {
    const onChange = setup()
    await userEvent.click(screen.getByRole("tab", { name: "Stats" }))
    expect(onChange).toHaveBeenCalledWith("stats")
  })

  it("ArrowRight and ArrowLeft move to the next and previous tab and wrap", () => {
    const onChange = setup("stats")
    const tabs = screen.getAllByRole("tab")
    fireEvent.keyDown(tabs[2], { key: "ArrowRight" })
    expect(onChange).toHaveBeenLastCalledWith("info")
    expect(tabs[0]).toHaveFocus()
    fireEvent.keyDown(tabs[0], { key: "ArrowLeft" })
    expect(onChange).toHaveBeenLastCalledWith("stats")
    expect(tabs[2]).toHaveFocus()
    fireEvent.keyDown(tabs[2], { key: "ArrowLeft" })
    expect(onChange).toHaveBeenLastCalledWith("similar")
    expect(tabs[1]).toHaveFocus()
  })

  it("Home and End jump to the first and last tab", () => {
    const onChange = setup("similar")
    const tabs = screen.getAllByRole("tab")
    fireEvent.keyDown(tabs[1], { key: "End" })
    expect(onChange).toHaveBeenLastCalledWith("stats")
    expect(tabs[2]).toHaveFocus()
    fireEvent.keyDown(tabs[2], { key: "Home" })
    expect(onChange).toHaveBeenLastCalledWith("info")
    expect(tabs[0]).toHaveFocus()
  })

  it("stops propagation of handled keys so document listeners do not act", () => {
    setup()
    const spy = vi.fn()
    document.addEventListener("keydown", spy)
    const tab = screen.getAllByRole("tab")[0]
    fireEvent.keyDown(tab, { key: "ArrowRight" })
    fireEvent.keyDown(tab, { key: "ArrowDown" })
    document.removeEventListener("keydown", spy)
    expect(spy).toHaveBeenCalledTimes(1)
  })

  it("value matching no tab keeps the first tab tabbable", () => {
    setup("gone")
    const tabs = screen.getAllByRole("tab")
    expect(tabs.map(t => t.getAttribute("tabindex"))).toEqual(["0", "-1", "-1"])
    expect(tabs.map(t => t.getAttribute("aria-selected"))).toEqual(["false", "false", "false"])
  })

  it("ArrowLeft with a value matching no tab goes to the last tab", () => {
    const onChange = setup("gone")
    const tabs = screen.getAllByRole("tab")
    fireEvent.keyDown(tabs[0], { key: "ArrowLeft" })
    expect(onChange).toHaveBeenLastCalledWith("stats")
    expect(tabs[2]).toHaveFocus()
  })

  it("an empty tabs list renders an empty tablist without crashing", () => {
    render(<Tabs tabs={[]} value="x" onChange={() => {}} idPrefix="e" aria-label="Empty" />)
    const list = screen.getByRole("tablist", { name: "Empty" })
    expect(list).toBeEmptyDOMElement()
    fireEvent.keyDown(list, { key: "ArrowRight" })
  })

  it("aria-controls of every tab equals panelId(prefix, id)", () => {
    setup()
    for (const [i, t] of screen.getAllByRole("tab").entries()) {
      expect(t).toHaveAttribute("aria-controls", panelId("m1", TABS[i].id))
    }
  })

  it("modified arrows are ignored and not stopped", () => {
    const onChange = setup()
    const spy = vi.fn()
    document.addEventListener("keydown", spy)
    const tab = screen.getAllByRole("tab")[0]
    fireEvent.keyDown(tab, { key: "ArrowLeft", altKey: true })
    fireEvent.keyDown(tab, { key: "ArrowRight", ctrlKey: true })
    fireEvent.keyDown(tab, { key: "ArrowRight", metaKey: true })
    document.removeEventListener("keydown", spy)
    expect(onChange).not.toHaveBeenCalled()
    expect(spy).toHaveBeenCalledTimes(3)
  })
})
