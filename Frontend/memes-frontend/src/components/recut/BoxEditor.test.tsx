import { fireEvent, render, screen } from "@testing-library/react"
import BoxEditor from "./BoxEditor"

// jsdom has no PointerEvent: a MouseEvent carries the coordinates the editor reads.
if (typeof window.PointerEvent === "undefined") {
  class PolyfilledPointerEvent extends MouseEvent {
    pointerId: number
    constructor(type: string, init: MouseEventInit & { pointerId?: number } = {}) {
      super(type, init)
      this.pointerId = init.pointerId ?? 1
    }
  }
  Object.defineProperty(window, "PointerEvent", { value: PolyfilledPointerEvent, configurable: true })
}

const size = { width: 1000, height: 800 }
const box = { x: 100, y: 100, w: 300, h: 200 }

beforeEach(() => {
  // The image is shown at half its natural size: one CSS pixel is two image pixels.
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(
    { left: 0, top: 0, width: 500, height: 400, right: 500, bottom: 400, x: 0, y: 0, toJSON() {} })
})

afterEach(() => vi.restoreAllMocks())

function renderEditor() {
  const onChange = vi.fn()
  render(<BoxEditor box={box} size={size} onChange={onChange} />)
  return { onChange, body: screen.getByTestId("recut-box-body"), group: screen.getByRole("group", { name: "Crop box editor" }) }
}

describe("BoxEditor", () => {
  it("shows the box with its eight handles", () => {
    const { group } = renderEditor()

    const handles = Array.from(group.querySelectorAll("[data-handle]")).map((el) => el.getAttribute("data-handle"))
    expect(handles.sort()).toEqual(["e", "n", "ne", "nw", "s", "se", "sw", "w"])
  })

  it("moves the box by the drag, scaled to image pixels", () => {
    const { onChange, body } = renderEditor()

    fireEvent.pointerDown(body, { clientX: 100, clientY: 100, pointerId: 1 })
    fireEvent.pointerMove(body, { clientX: 125, clientY: 90, pointerId: 1 })

    expect(onChange).toHaveBeenLastCalledWith({ x: 150, y: 80, w: 300, h: 200 })
  })

  it("resizes from a corner handle", () => {
    const { onChange, group } = renderEditor()
    const handle = group.querySelector('[data-handle="se"]') as HTMLElement

    fireEvent.pointerDown(handle, { clientX: 200, clientY: 150, pointerId: 1 })
    fireEvent.pointerMove(handle, { clientX: 210, clientY: 155, pointerId: 1 })

    expect(onChange).toHaveBeenLastCalledWith({ x: 100, y: 100, w: 320, h: 210 })
  })

  it("measures every move from where the drag started, so rounding never accumulates", () => {
    const { onChange, body } = renderEditor()

    fireEvent.pointerDown(body, { clientX: 100, clientY: 100, pointerId: 1 })
    fireEvent.pointerMove(body, { clientX: 101, clientY: 100, pointerId: 1 })
    fireEvent.pointerMove(body, { clientX: 125, clientY: 100, pointerId: 1 })

    expect(onChange).toHaveBeenLastCalledWith({ x: 150, y: 100, w: 300, h: 200 })
  })

  it("stops following the pointer once it is released", () => {
    const { onChange, body } = renderEditor()
    fireEvent.pointerDown(body, { clientX: 100, clientY: 100, pointerId: 1 })
    fireEvent.pointerUp(body, { clientX: 100, clientY: 100, pointerId: 1 })

    fireEvent.pointerMove(body, { clientX: 300, clientY: 300, pointerId: 1 })

    expect(onChange).not.toHaveBeenCalled()
  })

  it("ignores moves when no drag started", () => {
    const { onChange, body } = renderEditor()

    fireEvent.pointerMove(body, { clientX: 300, clientY: 300, pointerId: 1 })

    expect(onChange).not.toHaveBeenCalled()
  })
})
