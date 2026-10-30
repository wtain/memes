import { describe, expect, it } from "vitest"
import { isValidBox, moveBox, parseCoordinate, resizeBox, sameBox } from "./boxEdit"

const size = { width: 1000, height: 800 }
const box = { x: 100, y: 100, w: 300, h: 200 }

describe("moveBox", () => {
  it("shifts the box", () => {
    expect(moveBox(box, 50, -30, size)).toEqual({ x: 150, y: 70, w: 300, h: 200 })
  })
  it("stops at the right and bottom edges", () => {
    expect(moveBox(box, 900, 0, size).x).toBe(700)
    expect(moveBox(box, 0, 900, size).y).toBe(600)
  })
  it("stops at the left and top edges", () => {
    expect(moveBox(box, -500, -500, size)).toMatchObject({ x: 0, y: 0 })
  })
  it("rounds fractional deltas to whole pixels", () => {
    expect(moveBox(box, 10.6, 0, size).x).toBe(111)
  })
})

describe("resizeBox", () => {
  it("grows to the east", () => {
    expect(resizeBox(box, "e", 50, 0, size).w).toBe(350)
  })
  it("moves the west edge and shrinks", () => {
    expect(resizeBox(box, "w", 40, 0, size)).toMatchObject({ x: 140, w: 260 })
  })
  it("clamps a north-west drag at the image corner", () => {
    expect(resizeBox(box, "nw", -200, -200, size)).toEqual({ x: 0, y: 0, w: 400, h: 300 })
  })
  it("clamps a south-east drag at the opposite corner", () => {
    expect(resizeBox(box, "se", 5000, 5000, size)).toMatchObject({ w: 900, h: 700 })
  })
  it("never makes a side shorter than 16", () => {
    expect(resizeBox(box, "e", -290, 0, size).w).toBe(16)
    expect(resizeBox(box, "w", 290, 0, size)).toMatchObject({ x: 384, w: 16 })
    expect(resizeBox(box, "s", 0, -195, size).h).toBe(16)
  })
  it("clamps the north edge at the top", () => {
    expect(resizeBox(box, "n", 0, -500, size)).toMatchObject({ y: 0, h: 300 })
  })
  it("leaves the other axis alone for an edge handle", () => {
    expect(resizeBox(box, "e", 50, 400, size)).toMatchObject({ y: 100, h: 200 })
  })
})

describe("isValidBox", () => {
  it("accepts a box inside the image", () => {
    expect(isValidBox(box, size)).toBe(true)
  })
  it.each([
    ["a side under 16", { ...box, w: 15 }],
    ["a negative origin", { ...box, x: -1 }],
    ["an overflow to the right", { ...box, x: 800, w: 300 }],
    ["an overflow at the bottom", { ...box, h: 701 }],
    ["a fractional value", { ...box, x: 1.5 }],
  ])("rejects %s", (_name, candidate) => {
    expect(isValidBox(candidate, size)).toBe(false)
  })
})

describe("sameBox", () => {
  it("compares all four values", () => {
    expect(sameBox(box, { ...box })).toBe(true)
    expect(sameBox(box, { ...box, h: 201 })).toBe(false)
  })
})

describe("parseCoordinate", () => {
  it("reads a whole non-negative number", () => {
    expect(parseCoordinate(" 42 ")).toBe(42)
    expect(parseCoordinate("0")).toBe(0)
  })
  it.each(["", "-1", "1.5", "abc", "1e3"])("rejects %j", (text) => {
    expect(parseCoordinate(text)).toBeNull()
  })
})
