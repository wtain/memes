import type { Box, Size } from "./boxOverlay"

/** The shortest side the server accepts for an adjusted box (R6). */
export const MIN_SIDE = 16

export type Handle = "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw"

const clamp = (value: number, low: number, high: number): number => Math.max(low, Math.min(high, value))

/** The box moved by (dx, dy) image pixels, kept inside the image. */
export function moveBox(box: Box, dx: number, dy: number, size: Size): Box {
  return {
    ...box,
    x: clamp(box.x + Math.round(dx), 0, size.width - box.w),
    y: clamp(box.y + Math.round(dy), 0, size.height - box.h),
  }
}

/** The box with the edges named by `handle` moved by (dx, dy) image pixels, kept inside the image and at least MIN_SIDE wide and high. */
export function resizeBox(box: Box, handle: Handle, dx: number, dy: number, size: Size): Box {
  const moveX = Math.round(dx)
  const moveY = Math.round(dy)
  let left = box.x
  let right = box.x + box.w
  let top = box.y
  let bottom = box.y + box.h
  if (handle.includes("w")) left = clamp(left + moveX, 0, right - MIN_SIDE)
  if (handle.includes("e")) right = clamp(right + moveX, left + MIN_SIDE, size.width)
  if (handle.includes("n")) top = clamp(top + moveY, 0, bottom - MIN_SIDE)
  if (handle.includes("s")) bottom = clamp(bottom + moveY, top + MIN_SIDE, size.height)
  return { x: left, y: top, w: right - left, h: bottom - top }
}

/** What the server would accept for this image: whole numbers, inside the image, sides of at least MIN_SIDE. */
export function isValidBox(box: Box, size: Size): boolean {
  const whole = [box.x, box.y, box.w, box.h].every(Number.isInteger)
  return whole && box.x >= 0 && box.y >= 0 && box.w >= MIN_SIDE && box.h >= MIN_SIDE
    && box.x + box.w <= size.width && box.y + box.h <= size.height
}

export const sameBox = (a: Box, b: Box): boolean => a.x === b.x && a.y === b.y && a.w === b.w && a.h === b.h

/** A whole non-negative number from a text field, or null. */
export function parseCoordinate(text: string): number | null {
  const trimmed = text.trim()
  return /^\d+$/.test(trimmed) ? Number(trimmed) : null
}
