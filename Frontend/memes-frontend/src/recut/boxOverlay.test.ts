import { overlayStyle, removedShare } from "./boxOverlay"

describe("overlayStyle", () => {
  it("expresses the box in percentages of the source size", () => {
    expect(overlayStyle({ x: 10, y: 20, w: 50, h: 40 }, { width: 100, height: 200 }))
      .toEqual({ left: "10%", top: "10%", width: "50%", height: "20%" })
  })

  it("a box that fills the whole image covers all of it", () => {
    expect(overlayStyle({ x: 0, y: 0, w: 640, h: 480 }, { width: 640, height: 480 }))
      .toEqual({ left: "0%", top: "0%", width: "100%", height: "100%" })
  })

  it("keeps thirds readable", () => {
    expect(overlayStyle({ x: 1, y: 0, w: 1, h: 3 }, { width: 3, height: 3 }).left).toBe("33.3333%")
  })
})

describe("removedShare", () => {
  it("is the part of the source area outside the box", () => {
    expect(removedShare({ x: 0, y: 0, w: 50, h: 50 }, { width: 100, height: 100 })).toBe(0.75)
    expect(removedShare({ x: 0, y: 0, w: 100, h: 100 }, { width: 100, height: 100 })).toBe(0)
  })
})
