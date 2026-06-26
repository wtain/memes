import { useEffect, useRef, useState } from "react"
import { TagList } from "./TagList"
import type { MemesApi } from "../api/MemesApi"
import MemeCard from "./MemeCard"
import type { Concept, Meme } from "../types/generated/all"
import { ConceptRow } from "./ConceptRow"
import { useNavigate } from "react-router-dom"
import { useFetchById } from "../utils/useFetchById"

type Props = {
  meme: Meme
  memesApi: MemesApi
}

const MIN_ZOOM = 0.5
const MAX_ZOOM = 4
const ZOOM_STEP = 0.25
const FADE_DELAY_MS = 1500

export function MemeDetails({ meme, memesApi }: Props) {
  const [similarMemes, setSimilarMemes] = useState<Meme[]>([])
  const [concepts, setConcepts] = useState<Concept[]>([])
  const [isExcluded, setIsExcluded] = useState<boolean | null>(null)
  const [scale, setScale] = useState(1)
  const [offset, setOffset] = useState({ x: 0, y: 0 })
  const [controlsActive, setControlsActive] = useState(false)
  const [dragging, setDragging] = useState(false)

  const containerRef = useRef<HTMLDivElement>(null)
  const dragStart = useRef<{ mx: number; my: number; ox: number; oy: number } | null>(null)
  const fadeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const scaleRef = useRef(scale)  // kept in sync for use inside native listeners

  const navigate = useNavigate()

  useFetchById(meme.id, id => memesApi.similarMemes(id), resp => setSimilarMemes(resp.items ?? []))
  useFetchById(meme.id, id => memesApi.getTopConceptsForImage(id), resp => setConcepts(resp ?? []))
  useFetchById(meme.id, id => memesApi.getImageIsExcluded(id), setIsExcluded)

  function toggleExcluded() {
    const next = !isExcluded
    const call = next ? memesApi.markImageIsExcluded(meme.id) : memesApi.unmarkImageIsExcluded(meme.id)
    call.then(() => setIsExcluded(next))
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
    <div>
      {/* Image with zoom + pan */}
      <div
        ref={containerRef}
        className="mb-6 relative overflow-hidden rounded-xl select-none"
        style={{ cursor: dragging ? "grabbing" : scale > 1 ? "grab" : "default" }}
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={onMouseUp}
        onMouseLeave={onMouseUp}
      >
        <img
          src={memesApi.getImageUrl(meme)}
          alt={meme.id}
          draggable={false}
          className="w-full object-contain transition-transform duration-150 origin-center"
          style={{ transform: `scale(${scale}) translate(${offset.x / scale}px, ${offset.y / scale}px)` }}
        />

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

      {/* Metadata */}
      <div className="space-y-4 text-sm">
        <div>
          <strong>ID:</strong> 
            <a className="hover:underline" href={`/memes/${meme.id}`}>{meme.id}</a>
            <span
                onClick={() => {
                if ("clipboard" in navigator) navigator.clipboard.writeText(meme.id!)
                }}
                className="cursor-pointer hover:bg-gray-100 transition ml-2"
            >🗐</span>
            <br />
          <div
            onClick={() => {
              if ("clipboard" in navigator) navigator.clipboard.writeText(meme.originalFileName!)
            }}
            className="cursor-pointer hover:bg-gray-100 transition"
          >
            <strong>File name: </strong>{meme.originalFileName} <br />
          </div>
          <a
            href={memesApi.getImageUrl(meme)}
            download={meme.originalFileName ?? meme.id}
            className="inline-block mt-2 px-3 py-1 text-xs rounded border border-gray-300 hover:bg-gray-100 transition"
          >
            ⬇ Download
          </a>
        </div>

        <div>
          <strong>Text Lines:</strong>
          <ul className="list-disc ml-6">
            {meme.text!.map((line, i) => <li key={i}>{line}</li>)}
          </ul>
        </div>

        <div>
          <strong>Tags:</strong>
          <TagList tags={meme.tags!} />
        </div>

        <div className="flex items-center gap-2">
          <input
            id="excluded-toggle"
            type="checkbox"
            checked={isExcluded ?? false}
            disabled={isExcluded === null}
            onChange={toggleExcluded}
            className="w-4 h-4 cursor-pointer"
          />
          <label htmlFor="excluded-toggle" className="cursor-pointer select-none">
            Excluded
          </label>
        </div>

        <div>
          <table className="w-full border">
            <thead>
              <tr className="text-left bg-gray-100">
                <th className="p-3">ID</th>
                <th className="p-3">Name</th>
              </tr>
            </thead>
            <tbody>
              {concepts.map(concept => (
                <ConceptRow key={concept.id} concept={concept} onClick={() => navigate(`/concepts/${concept.id}`)} />
              ))}
            </tbody>
          </table>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
          {similarMemes.map(m => (
            <div key={m.id}>
              <MemeCard meme={m} memesApi={memesApi} onClick={() => navigate(`/memes/${m.id}`)} />
              {typeof m.cosineDistance === "number" && (
                <p className="text-center text-xs text-gray-400 mt-1">{m.cosineDistance.toFixed(2)}</p>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}