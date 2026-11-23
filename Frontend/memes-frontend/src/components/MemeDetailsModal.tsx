import { useEffect, useRef } from "react"
import type { MemesApi } from "../api/MemesApi"
import { Modal } from "./Modal"
import { MemeDetails } from "./MemeDetails"
import type { Meme } from "../types/generated/all"

/** The previous and next meme of the list the modal was opened from; a missing callback disables its button. */
export type MemeNeighbors = {
  prev?: Meme
  next?: Meme
  onPrev?: () => void
  onNext?: () => void
}

type Props = {
  meme: Meme
  memesApi: MemesApi
  onClose: () => void
  neighbors?: MemeNeighbors
}

// Left/Right keep their own meaning inside a text field, a select, an editable region and a tab list.
function ignoresArrows(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false
  if (target.closest("input, textarea, select, [role='tab']")) return true
  const editable = target.closest("[contenteditable]")
  return editable !== null && editable.getAttribute("contenteditable") !== "false"
}

const ARROW_BUTTON =
  "absolute top-1/2 -translate-y-1/2 z-10 flex h-10 w-10 items-center justify-center rounded-full bg-surface text-fg shadow-md ring-1 ring-inset ring-line-strong " +
  "cursor-pointer focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand motion-safe:transition-colors hover:bg-surface-sunken pointer-coarse:h-11 pointer-coarse:w-11 " +
  "disabled:opacity-40 disabled:pointer-events-none"

function Chevron({ direction }: { direction: "left" | "right" }) {
  return (
    <svg viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.75"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      <path d={direction === "left" ? "M12.5 4 6.5 10l6 6" : "M7.5 4l6 6-6 6"} />
    </svg>
  )
}

export function MemeDetailsModal({ meme, memesApi, onClose, neighbors }: Props) {
  const prevMeme = neighbors?.prev
  const nextMeme = neighbors?.next
  const onPrev = neighbors?.onPrev
  const onNext = neighbors?.onNext
  const hasNeighbors = neighbors !== undefined

  useEffect(() => {
    if (!hasNeighbors) return
    const handler = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return
      if (ignoresArrows(e.target)) return
      const step = e.key === "ArrowLeft" ? onPrev : onNext
      if (!step) return
      e.preventDefault()
      step()
    }
    document.addEventListener("keydown", handler)
    return () => document.removeEventListener("keydown", handler)
  }, [hasNeighbors, onPrev, onNext])

  // A focused arrow button that becomes disabled (stepping onto the first or last item) would drop focus to
  // the body, and the next Tab would leave the dialog: hand focus to the other arrow instead.
  const prevButton = useRef<HTMLButtonElement>(null)
  const nextButton = useRef<HTMLButtonElement>(null)
  const lastFocused = useRef<"prev" | "next" | null>(null)
  useEffect(() => {
    const active = document.activeElement
    const lost = active === null || active === document.body
    if (!onNext && onPrev && (active === nextButton.current || (lost && lastFocused.current === "next"))) {
      prevButton.current?.focus()
    } else if (!onPrev && onNext && (active === prevButton.current || (lost && lastFocused.current === "prev"))) {
      nextButton.current?.focus()
    }
  }, [onPrev, onNext])

  // Warm the cache so a step shows its image at once; the Image objects are kept so a browser cannot drop the request.
  const preloaded = useRef<HTMLImageElement[]>([])
  useEffect(() => {
    const images: HTMLImageElement[] = []
    for (const neighbour of [nextMeme, prevMeme]) {
      if (!neighbour) continue
      const img = new Image()
      img.src = memesApi.getImageUrl(neighbour)
      images.push(img)
    }
    preloaded.current = images
  }, [nextMeme, prevMeme, memesApi])

  return (
    <Modal onClose={onClose} title="Meme Details" size="wide" titleHidden>
      <MemeDetails
        meme={meme}
        memesApi={memesApi}
        layout="modal"
        imageOverlay={hasNeighbors ? (
          <>
            <button type="button" ref={prevButton} aria-label="Previous meme" disabled={!onPrev} onClick={onPrev}
              onFocus={() => { lastFocused.current = "prev" }} onBlur={e => { if (e.relatedTarget) lastFocused.current = null }}
              className={`${ARROW_BUTTON} left-2`}>
              <Chevron direction="left" />
            </button>
            <button type="button" ref={nextButton} aria-label="Next meme" disabled={!onNext} onClick={onNext}
              onFocus={() => { lastFocused.current = "next" }} onBlur={e => { if (e.relatedTarget) lastFocused.current = null }}
              className={`${ARROW_BUTTON} right-2`}>
              <Chevron direction="right" />
            </button>
          </>
        ) : undefined}
      />
    </Modal>
  )
}
