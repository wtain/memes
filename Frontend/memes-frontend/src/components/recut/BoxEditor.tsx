import { useRef } from "react"
import type { CSSProperties, PointerEvent as ReactPointerEvent } from "react"
import { moveBox, resizeBox } from "../../recut/boxEdit"
import type { Handle } from "../../recut/boxEdit"
import { overlayStyle } from "../../recut/boxOverlay"
import type { Box, Size } from "../../recut/boxOverlay"

type Props = { box: Box; size: Size; onChange: (box: Box) => void }
type Drag = { kind: "move" | Handle; startX: number; startY: number; start: Box }

// Each handle sits on the box outline: its position as a share of the box, and the cursor it shows.
const HANDLES: { handle: Handle; left: string; top: string; cursor: string }[] = [
  { handle: "nw", left: "0%", top: "0%", cursor: "nwse-resize" },
  { handle: "n", left: "50%", top: "0%", cursor: "ns-resize" },
  { handle: "ne", left: "100%", top: "0%", cursor: "nesw-resize" },
  { handle: "e", left: "100%", top: "50%", cursor: "ew-resize" },
  { handle: "se", left: "100%", top: "100%", cursor: "nwse-resize" },
  { handle: "s", left: "50%", top: "100%", cursor: "ns-resize" },
  { handle: "sw", left: "0%", top: "100%", cursor: "nesw-resize" },
  { handle: "w", left: "0%", top: "50%", cursor: "ew-resize" },
]

function handleStyle(left: string, top: string, cursor: string): CSSProperties {
  return { left, top, cursor, transform: "translate(-50%, -50%)" }
}

/** A draggable crop rectangle over the source image: drag the body to move it, a handle to resize it. Mounted inside the
 * relatively positioned wrapper of the image, so it covers exactly the displayed image. */
export default function BoxEditor({ box, size, onChange }: Props) {
  const container = useRef<HTMLDivElement>(null)
  const drag = useRef<Drag | null>(null)  // only touched in handlers

  function begin(event: ReactPointerEvent<HTMLElement>, kind: Drag["kind"]) {
    event.stopPropagation()
    drag.current = { kind, startX: event.clientX, startY: event.clientY, start: box }
    event.currentTarget.setPointerCapture?.(event.pointerId)
  }

  function onMove(event: ReactPointerEvent<HTMLElement>) {
    const active = drag.current
    const rect = container.current?.getBoundingClientRect()
    if (active === null || !rect || rect.width === 0 || rect.height === 0) return
    // Always from the box the drag started with, so rounding never accumulates over many small moves.
    const dx = ((event.clientX - active.startX) * size.width) / rect.width
    const dy = ((event.clientY - active.startY) * size.height) / rect.height
    onChange(active.kind === "move" ? moveBox(active.start, dx, dy, size) : resizeBox(active.start, active.kind, dx, dy, size))
  }

  function onEnd() {
    drag.current = null
  }

  return (
    <div
      ref={container}
      role="group"
      aria-label="Crop box editor"
      className="absolute inset-0"
      onPointerMove={onMove}
      onPointerUp={onEnd}
      onPointerCancel={onEnd}
    >
      <div
        data-testid="recut-box-body"
        className="absolute cursor-move touch-none border-2 border-red-500 bg-red-500/10"
        style={overlayStyle(box, size)}
        onPointerDown={(event) => begin(event, "move")}
      >
        {HANDLES.map(({ handle, left, top, cursor }) => (
          <div
            key={handle}
            data-handle={handle}
            className="absolute h-3 w-3 touch-none border border-red-700 bg-white"
            style={handleStyle(left, top, cursor)}
            onPointerDown={(event) => begin(event, handle)}
          />
        ))}
      </div>
    </div>
  )
}
