import { useEffect, useRef, useState } from "react"
import { TagList } from "./TagList"
import type { MemesApi } from "../api/MemesApi"
import MemeCard from "./MemeCard"
import type { Concept, ImageDescription, Meme } from "../types/generated/all"
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

function humanizePromptKey(promptKey: string): string {
  const spaced = promptKey.replace(/_/g, " ")
  return spaced.charAt(0).toUpperCase() + spaced.slice(1)
}

export function MemeDetails({ meme, memesApi }: Props) {
  const [similarMemes, setSimilarMemes] = useState<Meme[]>([])
  const [similarSource, setSimilarSource] = useState<"image" | "description">("image")
  const [concepts, setConcepts] = useState<Concept[]>([])
  const [descriptions, setDescriptions] = useState<ImageDescription[]>([])
  const [isFlagged, setIsFlagged] = useState<boolean | null>(null)
  const [scale, setScale] = useState(1)
  const [offset, setOffset] = useState({ x: 0, y: 0 })
  const [controlsActive, setControlsActive] = useState(false)
  const [dragging, setDragging] = useState(false)

  const containerRef = useRef<HTMLDivElement>(null)
  const dragStart = useRef<{ mx: number; my: number; ox: number; oy: number } | null>(null)
  const fadeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const scaleRef = useRef(scale)  // kept in sync for use inside native listeners

  const navigate = useNavigate()

  useFetchById(
    `${meme.id}:${similarSource}`,
    () => memesApi.similarMemes(meme.id, similarSource),
    resp => setSimilarMemes(resp.items ?? []),
    () => setSimilarMemes([]),
  )
  useFetchById(meme.id, id => memesApi.getTopConceptsForImage(id), resp => setConcepts(resp ?? []))
  useFetchById(meme.id, id => memesApi.getDescriptions(id), setDescriptions)
  useFetchById(meme.id, id => memesApi.getImageIsFlagged(id), setIsFlagged)

  function toggleFlagged() {
    const next = !isFlagged
    const call = next ? memesApi.markImageIsFlagged(meme.id) : memesApi.unmarkImageIsFlagged(meme.id)
    call.then(() => setIsFlagged(next))
  }

  function setDescriptionFeedback(promptKey: string, action: "approve" | "reject") {
    memesApi.setDescriptionFeedback(meme.id, promptKey, action).then(resp => {
      setDescriptions(prev => prev.map(d =>
        d.promptKey === promptKey ? { ...d, feedback: resp.feedback } : d
      ))
    })
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
          <strong>Descriptions:</strong>
          {descriptions.length === 0 ? (
            <p className="text-gray-400">No description available</p>
          ) : (
            <ul className="ml-2 space-y-2">
              {descriptions.map(d => (
                <li key={d.promptKey}>
                  <span className="font-medium">{humanizePromptKey(d.promptKey)}:</span> {d.text}
                  <button
                    onClick={() => setDescriptionFeedback(d.promptKey, "approve")}
                    aria-label={`Approve ${humanizePromptKey(d.promptKey)}`}
                    className={`ml-2 px-1.5 py-0.5 text-xs rounded border ${d.feedback === "approved" ? "bg-green-600 text-white border-green-600" : "border-gray-300 text-gray-500 hover:bg-gray-100"}`}
                  >
                    👍
                  </button>
                  <button
                    onClick={() => setDescriptionFeedback(d.promptKey, "reject")}
                    aria-label={`Reject ${humanizePromptKey(d.promptKey)}`}
                    className={`ml-1 px-1.5 py-0.5 text-xs rounded border ${d.feedback === "rejected" ? "bg-red-600 text-white border-red-600" : "border-gray-300 text-gray-500 hover:bg-gray-100"}`}
                  >
                    👎
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div>
          <strong>Tags:</strong>
          <TagList tags={meme.tags!} />
        </div>

        <div className="flex items-center gap-2">
          <input
            id="flagged-toggle"
            type="checkbox"
            checked={isFlagged ?? false}
            disabled={isFlagged === null}
            onChange={toggleFlagged}
            className="w-4 h-4 cursor-pointer"
          />
          <label htmlFor="flagged-toggle" className="cursor-pointer select-none">
            Flagged
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

        <div>
          <div className="flex gap-2 mb-2">
            <button
              onClick={() => setSimilarSource("image")}
              className={`px-3 py-1 text-xs rounded border ${similarSource === "image" ? "bg-gray-800 text-white border-gray-800" : "border-gray-300 text-gray-600 hover:bg-gray-100"}`}
            >
              Visual
            </button>
            <button
              onClick={() => setSimilarSource("description")}
              className={`px-3 py-1 text-xs rounded border ${similarSource === "description" ? "bg-gray-800 text-white border-gray-800" : "border-gray-300 text-gray-600 hover:bg-gray-100"}`}
            >
              Semantic
            </button>
          </div>
          {similarMemes.length === 0 ? (
            <p className="text-gray-400 text-sm">
              {similarSource === "description"
                ? "No semantic similarity available for this image yet"
                : "No similar images found"}
            </p>
          ) : (
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
          )}
        </div>
      </div>
    </div>
  )
}