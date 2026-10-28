export type Box = { x: number; y: number; w: number; h: number }
export type Size = { width: number; height: number }

const percent = (part: number, whole: number): string => `${Number(((part * 100) / whole).toFixed(4))}%`

/** The box as CSS percentages of the image it lies on, so the overlay stays right at any display size. */
export function overlayStyle(box: Box, size: Size): { left: string; top: string; width: string; height: string } {
  return {
    left: percent(box.x, size.width),
    top: percent(box.y, size.height),
    width: percent(box.w, size.width),
    height: percent(box.h, size.height),
  }
}

/** Share of the source area that lies outside the box (0 to 1). */
export function removedShare(box: Box, size: Size): number {
  return 1 - (box.w * box.h) / (size.width * size.height)
}
