import { useId, useRef, useState } from "react"
import { TagList } from "./TagList"
import { CardComicsAction } from "./comics/CardComicsAction"
import { useFeature } from "../features/useFeature"
import { Badge, Button, Card, FlagIcon } from "../ui"
import type { MemesApi } from "../api/MemesApi"
import type { Meme } from "../types/generated/all"

type Props = {
  meme: Meme
  memesApi: MemesApi
  onClick?: () => void
  variant?: "square" | "full"
  selectable?: boolean
  selected?: boolean
  onToggleSelect?: () => void
  isKeeper?: boolean
  onSetKeeper?: () => void
  similarity?: number
  /** Shows the "Add to comics" button; needs a router (it can link to the editor). */
  comicsAction?: boolean
  /** Target of the image link (default /memes/:id). A plain click still runs onClick. */
  href?: string
}

const REVEAL =
  "opacity-0 motion-safe:transition-opacity motion-safe:duration-150 group-hover:opacity-100 group-focus-within:opacity-100 pointer-coarse:opacity-100"

export default function MemeCard({ meme, memesApi, onClick, variant = "square", selectable = false, selected = false, onToggleSelect, isKeeper = false, onSetKeeper, similarity, comicsAction = false, href }: Props) {
  const curator = useFeature("curator")
  const comicsOn = useFeature("comics")
  const [showText, setShowText] = useState(false)
  const [isFlagged, setIsFlagged] = useState(meme.flagged ?? false)
  const [copied, setCopied] = useState(false)
  const copyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const descriptionId = useId()

  const textLines = meme.text ?? []
  const description = textLines.join(" ").slice(0, 120)
  const showFlag = curator
  const showComics = comicsAction && comicsOn
  const showBar = showFlag || showComics

  function copyId() {
    navigator.clipboard.writeText(meme.id).then(() => {
      setCopied(true)
      if (copyTimerRef.current) clearTimeout(copyTimerRef.current)
      copyTimerRef.current = setTimeout(() => setCopied(false), 1000)
    })
  }

  function toggleFlag() {
    if (isFlagged) {
      memesApi.unmarkImageIsFlagged(meme.id).then(() => setIsFlagged(false))
    } else {
      memesApi.markImageIsFlagged(meme.id).then(() => setIsFlagged(true))
    }
  }

  function onImageClick(e: React.MouseEvent<HTMLAnchorElement>) {
    if (showText || !onClick) return
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
    e.preventDefault()
    onClick()
  }

  return (
    <Card
      interactive
      data-testid="card-root"
      className={`group relative flex h-full flex-col ${curator && isFlagged ? "ring-2! ring-danger!" : ""}`}
    >
      <div className="relative">
        <a href={href ?? `/memes/${meme.id}`} onClick={onImageClick} className="block focus-visible:outline-offset-[-2px]">
          <img
            src={memesApi.getImageUrl(meme)}
            alt="Meme"
            aria-describedby={description ? descriptionId : undefined}
            className={`w-full object-cover ${variant === "square" ? "aspect-square" : ""}`}
            loading="lazy"
          />
        </a>
        {description && <span id={descriptionId} className="sr-only">{description}</span>}

        {curator && isFlagged && (
          <Badge tone="danger" className="absolute left-2 top-2">Flagged</Badge>
        )}

        {showBar && (
          <div className={`absolute right-2 top-2 flex items-center gap-1 rounded-md bg-scrim has-[[data-keep-visible]]:opacity-100 ${REVEAL}`}>
            {/* SLOT item 3: copy / download / open-link actions go here, before the curator group */}
            <div role="group" aria-label="Curator actions" className="flex items-center gap-1">
              {showFlag && (
                <Button
                  iconOnly
                  size="sm"
                  variant="ghost"
                  pressed={isFlagged}
                  aria-label="Flag"
                  className={isFlagged ? "text-on-danger! bg-danger!" : "text-white! hover:bg-white/20"}
                  onClick={toggleFlag}
                >
                  <FlagIcon />
                </Button>
              )}
              {showComics && <CardComicsAction memesApi={memesApi} imageId={meme.id} compact />}
            </div>
          </div>
        )}

        {textLines.length > 0 && (
          <Button
            size="sm"
            variant="ghost"
            className={`absolute bottom-2 right-2 bg-scrim text-white! hover:bg-black/70 ${REVEAL}`}
            onClick={e => { e.stopPropagation(); setShowText(true) }}
          >
            OCR
          </Button>
        )}

        {showText && (
          <div
            role="button"
            tabIndex={0}
            className="absolute inset-0 bg-black/70 p-3 text-sm text-white overflow-y-auto cursor-pointer"
            onClick={e => { e.stopPropagation(); setShowText(false) }}
            onKeyDown={e => {
              if (e.key === "Enter" || e.key === " " || e.key === "Escape") {
                e.preventDefault()
                setShowText(false)
              }
            }}
          >
            <div className="font-semibold mb-2">OCR <span className="text-xs font-normal opacity-60">(click to close)</span></div>
            <ul className="space-y-1">
              {textLines.map((line, i) => (
                <li key={i} className="opacity-90">{line}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
      {meme.clusterId != null && (
        <div className="px-4 py-1 text-xs text-gray-500 border-b flex items-center gap-2">
          <span>Cluster {meme.clusterId}</span>
          <button
            onClick={copyId}
            className="hover:text-gray-800 transition-colors cursor-pointer"
            title="Copy image ID"
          >🔗</button>
          <span className={`transition-opacity duration-500 ${copied ? "opacity-100" : "opacity-0"}`}>
            Copied
          </span>
        </div>
      )}
      {selectable && (
        <div className="px-4 py-1 flex items-center gap-3 border-b">
          <label className="flex items-center gap-1 text-xs">
            <input
              type="checkbox"
              aria-label="Select"
              checked={selected}
              onChange={() => onToggleSelect?.()}
            />
            Select
          </label>
          {selected && (
            <label className="flex items-center gap-1 text-xs">
              <input
                type="radio"
                name={`keeper-${meme.clusterId ?? "unknown"}`}
                aria-label="Keeper"
                checked={isKeeper}
                onChange={() => onSetKeeper?.()}
              />
              Keeper
            </label>
          )}
        </div>
      )}
      {similarity != null && (
        <div
          className="px-4 py-1 text-xs text-gray-500 border-b"
          title="Highest OCR-caption overlap with any currently-selected sibling in this cluster"
        >
          Caption match: {Math.round(similarity * 100)}%
        </div>
      )}
      <div data-testid="card-footer" className="mt-auto flex h-10 items-center overflow-hidden px-2">
        <TagList tags={meme.tags ?? []} variant="compact" />
      </div>
    </Card>
  )
}
