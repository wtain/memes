import { useEffect, useRef, useState, type ReactNode } from "react"

const MIN_ZOOM = 0.5
const MAX_ZOOM = 4
const ZOOM_STEP = 0.25
const FADE_DELAY_MS = 1500

type Props = {
  src: string
  alt: string
  /** Zoom and pan reset when this changes (the meme id); defaults to the image URL. */
  resetKey?: string
  /** Rendered inside the image box, above the image (the quick-look arrows). */
  overlay?: ReactNode
  /** Tailwind max-height class for the image. */
  imageMaxHeight?: string
}

/** The zoomable, pannable meme image (Ctrl+wheel, +/- buttons, drag). */
export function MemeImageViewer({ src, alt, resetKey = src, overlay, imageMaxHeight = "max-h-[75vh]" }: Props) {
  const [scale, setScale] = useState(1)
  const [offset, setOffset] = useState({ x: 0, y: 0 })
  const [controlsActive, setControlsActive] = useState(false)
  const [dragging, setDragging] = useState(false)

  const containerRef = useRef<HTMLDivElement>(null)
  const dragStart = useRef<{ mx: number; my: number; ox: number; oy: number } | null>(null)
  const fadeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const scaleRef = useRef(scale)  // kept in sync for use inside native listeners

  // A different image starts at 100% and centred (the viewer instance is reused when stepping memes).
  const [shownKey, setShownKey] = useState(resetKey)
  if (shownKey !== resetKey) {
    setShownKey(resetKey)
    setScale(1)
    setOffset({ x: 0, y: 0 })
  }

  function bumpControls() {
    setControlsActive(true)
    if (fadeTimerRef.current) clearTimeout(fadeTimerRef.current)
    fadeTimerRef.current = setTimeout(() => setControlsActive(false), FADE_DELAY_MS)
  }

  // Native wheel listener with passive:false so preventDefault() actually works
  useEffect(() => {
    const el = containerRef.current
    if (!el) return

    function onWheel(e: WheelEvent) {
        if (e.ctrlKey){
            e.preventDefault()
            const delta = e.deltaY < 0 ? ZOOM_STEP : -ZOOM_STEP
            const next = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, Math.round((scaleRef.current + delta) * 100) / 100))
            scaleRef.current = next
            setScale(next)
            if (next === 1) setOffset({ x: 0, y: 0 })
            bumpControls()
        }
    }

    el.addEventListener("wheel", onWheel, { passive: false })
    return () => el.removeEventListener("wheel", onWheel)
  }, [])

  // Keep scaleRef in sync
  useEffect(() => { scaleRef.current = scale }, [scale])

  function zoom(delta: number) {
    const next = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, Math.round((scaleRef.current + delta) * 100) / 100))
    scaleRef.current = next
    setScale(next)
    if (next === 1) setOffset({ x: 0, y: 0 })
    bumpControls()
  }

  function onMouseDown(e: React.MouseEvent) {
    if (e.button !== 0) return
    e.preventDefault()
    dragStart.current = { mx: e.clientX, my: e.clientY, ox: offset.x, oy: offset.y }
    setDragging(true)
  }

  function onMouseMove(e: React.MouseEvent) {
    if (!dragStart.current) return
    setOffset({
      x: dragStart.current.ox + (e.clientX - dragStart.current.mx),
      y: dragStart.current.oy + (e.clientY - dragStart.current.my),
    })
  }

  function onMouseUp() {
    dragStart.current = null
    setDragging(false)
  }

  const controlsOpacity = controlsActive ? "opacity-80" : "opacity-50"

  return (
    <div
      ref={containerRef}
      className="relative overflow-hidden rounded-xl select-none"
      style={{ cursor: dragging ? "grabbing" : scale > 1 ? "grab" : "default" }}
      onMouseDown={onMouseDown}
      onMouseMove={onMouseMove}
      onMouseUp={onMouseUp}
      onMouseLeave={onMouseUp}
    >
      <img
        src={src}
        alt={alt}
        draggable={false}
        className={`mx-auto w-full ${imageMaxHeight} object-contain transition-transform duration-150 origin-center`}
        style={{ transform: `scale(${scale}) translate(${offset.x / scale}px, ${offset.y / scale}px)` }}
      />

      {overlay && (
        <div onMouseDown={e => e.stopPropagation()}>{overlay}</div>
      )}

      {/* Zoom controls */}
      <div
        className={`absolute top-2 right-2 flex flex-col items-center gap-1 transition-opacity duration-500 ${controlsOpacity}`}
        onMouseDown={e => e.stopPropagation()} // prevent drag starting from buttons
      >
        <button
          onClick={() => zoom(ZOOM_STEP)}
          disabled={scale >= MAX_ZOOM}
          className="w-7 h-7 rounded bg-black/60 text-white text-lg flex items-center justify-center hover:bg-black/80 disabled:opacity-30"
        >+</button>
        <button
          onClick={() => zoom(-ZOOM_STEP)}
          disabled={scale <= MIN_ZOOM}
          className="w-7 h-7 rounded bg-black/60 text-white text-lg flex items-center justify-center hover:bg-black/80 disabled:opacity-30"
        >−</button>
        <span className="text-xs text-white bg-black/60 rounded px-1 py-0.5 mt-0.5">
          {Math.round(scale * 100)}%
        </span>
      </div>
    </div>
  )
}
