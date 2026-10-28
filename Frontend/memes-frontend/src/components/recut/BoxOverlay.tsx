import { overlayStyle } from "../../recut/boxOverlay"
import type { Box, Size } from "../../recut/boxOverlay"

export default function BoxOverlay({ box, size }: { box: Box; size: Size }) {
  return (
    <div
      data-testid="recut-box"
      aria-hidden="true"
      className="pointer-events-none absolute border-2 border-red-500 bg-red-500/10"
      style={overlayStyle(box, size)}
    />
  )
}
