import { useCallback, useEffect, useState } from "react"
import { useSearchParams } from "react-router-dom"
import type { MemesApi } from "../api/MemesApi"
import AdjustPanel from "../components/recut/AdjustPanel"
import BeforeAfter from "../components/recut/BeforeAfter"
import ProposalInfo from "../components/recut/ProposalInfo"
import QueueStrip from "../components/recut/QueueStrip"
import ReviewStats from "../components/recut/ReviewStats"
import type { Box, Size } from "../recut/boxOverlay"
import { useRecutQueue } from "../recut/useRecutQueue"
import { useRecutStats } from "../recut/useRecutStats"

type Props = { memesApi: MemesApi }

const LOAD_AHEAD = 5

const CONFLICT_TEXT: Record<string, string> = {
  already_decided: "Already decided",
  cut_missing: "The cut image is missing",
  source_missing: "The source image is missing",
  source_not_active: "The source image is no longer active",
  source_flagged: "The source image is flagged, so it can't be approved",
  source_changed: "The source file changed after detection, so the box can't be applied",
  source_unreadable: "The source file can't be read",
}

const ADJUST_NOTE: Record<"adjusted" | "invalid" | "failed", string> = {
  adjusted: "Adjusted. Derived data (OCR, descriptions, embeddings) is stale until recut_pipeline has run; run it before approving.",
  invalid: "That box does not fit this image",
  failed: "Could not adjust the box",
}

export default function RecutReviewPage({ memesApi }: Props) {
  const [params, setParams] = useSearchParams()
  const openId = params.get("cut")
  const queue = useRecutQueue(memesApi, openId)
  const { items, total, listStatus, loadMore, detail, decision, decide, adjust, adjusting, settleConflict } = queue

  // Edit-mode state is keyed by the proposal it belongs to, so moving to another proposal drops it without an effect.
  const [draftState, setDraftState] = useState<{ id: string; box: Box } | null>(null)
  const [sizeState, setSizeState] = useState<{ id: string; size: Size } | null>(null)
  const [noteState, setNoteState] = useState<{ id: string; kind: keyof typeof ADJUST_NOTE } | null>(null)
  const draft = draftState !== null && draftState.id === openId ? draftState.box : null
  const size = sizeState !== null && sizeState.id === openId ? sizeState.size : null
  const note = noteState !== null && noteState.id === openId ? noteState.kind : null
  const editing = draft !== null

  const open = useCallback((id: string, replace = false) => setParams({ cut: id }, { replace }), [setParams])

  // `total` drops by one after every decision and changes on a reload, so the statistics are requested again then.
  const stats = useRecutStats(memesApi, listStatus === "ready" ? total : null)

  const saving = decision.status === "saving"
  const waiting = detail?.status === "ready" && detail.data.state === "proposed" && !saving && !adjusting
  const decidable = waiting && !editing
  const onDecide = useCallback(async (kind: "approve" | "reject") => {
    const result = await decide(kind)
    if (result.outcome === "done" && result.next !== null) open(result.next)
  }, [decide, open])

  const firstId = items[0]?.id ?? null
  const detailStatus = detail?.status
  useEffect(() => {
    if (listStatus !== "ready" || firstId === null || firstId === openId) return
    if (openId === null || detailStatus === "missing") open(firstId, true)
  }, [listStatus, firstId, openId, detailStatus, open])

  // After a 409 the queue is reloaded: move to its head once, then leave the reviewer free to navigate.
  const redirecting = decision.status === "conflict" && decision.redirect
  useEffect(() => {
    if (!redirecting || listStatus !== "ready") return
    if (firstId !== null && firstId !== openId) open(firstId, true)
    settleConflict()
  }, [redirecting, listStatus, firstId, openId, open, settleConflict])

  const position = items.findIndex((item) => item.id === openId)
  useEffect(() => {
    if (position >= 0 && position >= items.length - LOAD_AHEAD) loadMore()
  }, [position, items.length, loadMore])

  const previousId = detail?.status === "ready" ? detail.data.previousId : null
  const nextId = detail?.status === "ready" ? detail.data.nextId : null
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return  // Alt+arrow is the browser's back/forward
      if (editing && event.key === "Escape" && !adjusting) setDraftState(null)
      if (editing) return  // while the box is edited no key decides or navigates
      const target = event.target as HTMLElement | null
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable)) return
      if (event.key === "ArrowLeft" && previousId !== null) open(previousId)
      if (event.key === "ArrowRight" && nextId !== null) open(nextId)
      // A held key repeats: it must never decide the proposal that auto-advance just opened (there is no undo).
      if (event.repeat) return
      if (decidable && event.key.toLowerCase() === "a") void onDecide("approve")
      if (decidable && event.key.toLowerCase() === "r") void onDecide("reject")
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [previousId, nextId, open, decidable, onDecide, editing, adjusting])

  async function onApply(proposalId: string) {
    if (draft === null) return
    setNoteState(null)
    const result = await adjust(draft)
    if (result.outcome === "done") {
      setDraftState(null)
      setNoteState({ id: proposalId, kind: "adjusted" })
    } else if (result.outcome === "invalid" || result.outcome === "failed") {
      setNoteState({ id: proposalId, kind: result.outcome })
    } else if (result.outcome === "conflict") {
      setDraftState(null)
    }
  }

  let body: React.ReactNode
  if (listStatus === "loading") {
    body = <p role="status">Loading proposals...</p>
  } else if (listStatus === "failed") {
    body = (
      <div role="alert">
        <p>Could not load the proposals.</p>
        <button type="button" onClick={queue.reload} className="mt-2 min-h-11 rounded border px-3">Retry</button>
      </div>
    )
  } else if (detail?.status === "ready") {
    const proposal = detail.data
    body = (
      <>
        <div className="mb-3 flex items-center gap-3">
          <button type="button" disabled={previousId === null} onClick={() => previousId !== null && open(previousId)}
                  className="min-h-11 rounded border px-3 disabled:opacity-50">Previous</button>
          <span className="text-sm">{position >= 0 ? `${position + 1} of ${total}` : `${total} waiting`}</span>
          <button type="button" disabled={nextId === null} onClick={() => nextId !== null && open(nextId)}
                  className="min-h-11 rounded border px-3 disabled:opacity-50">Next</button>
        </div>
        <BeforeAfter
          key={`${proposal.id}:${proposal.cutImageId}`}
          sourceUrl={memesApi.getImageUrlById(proposal.sourceImageId)}
          cutUrl={memesApi.getImageUrlById(proposal.cutImageId)}
          box={proposal.box}
          draft={draft}
          onDraftChange={(box) => setDraftState({ id: proposal.id, box })}
          onSize={(loaded) => setSizeState({ id: proposal.id, size: loaded })}
        />
        {draft !== null && size !== null && (
          <AdjustPanel
            draft={draft}
            size={size}
            current={proposal.box}
            detectedBox={proposal.detectedBox}
            disabled={adjusting}
            onChange={(box) => setDraftState({ id: proposal.id, box })}
            onApply={() => void onApply(proposal.id)}
            onCancel={() => setDraftState(null)}
            onReset={() => { if (proposal.detectedBox !== null) setDraftState({ id: proposal.id, box: proposal.detectedBox }) }}
          />
        )}
        {note === "adjusted" && <p role="status" className="mt-3 text-sm">{ADJUST_NOTE.adjusted}</p>}
        {(note === "invalid" || note === "failed") && <p role="alert" className="mt-3 text-sm">{ADJUST_NOTE[note]}</p>}
        <div className="mt-4"><ProposalInfo detail={proposal} /></div>
        <div className="mt-4 flex items-center gap-3">
          <button type="button" disabled={!decidable} aria-keyshortcuts="a" onClick={() => void onDecide("approve")}
                  className="min-h-11 rounded border px-3 disabled:opacity-50">Approve</button>
          <button type="button" disabled={!decidable} aria-keyshortcuts="r" onClick={() => void onDecide("reject")}
                  className="min-h-11 rounded border px-3 disabled:opacity-50">Reject</button>
          <button type="button" disabled={!decidable || size === null}
                  onClick={() => { setNoteState(null); setDraftState({ id: proposal.id, box: proposal.box }) }}
                  className="min-h-11 rounded border px-3 disabled:opacity-50">Adjust</button>
        </div>
        <QueueStrip items={items} openId={openId} imageUrl={(id) => memesApi.getImageUrlById(id)} onOpen={(id) => open(id)} />
      </>
    )
  } else if (items.length === 0) {
    body = <p>No proposals waiting for review</p>
  } else if (detail?.status === "missing" && firstId === openId) {
    // Not redirected (it is already the first one), so say so instead of loading forever.
    body = (
      <div role="alert">
        <p>This proposal is no longer available</p>
        <button type="button" onClick={queue.reload} className="mt-2 min-h-11 rounded border px-3">Retry</button>
      </div>
    )
  } else if (detail?.status === "failed") {
    body = (
      <div role="alert">
        <p>Could not load this proposal.</p>
        <button type="button" onClick={queue.reload} className="mt-2 min-h-11 rounded border px-3">Retry</button>
      </div>
    )
  } else {
    body = <p role="status">Loading proposal...</p>
  }

  return (
    <div>
      <h1 className="mb-4 text-xl font-semibold">Recut review</h1>
      {decision.status === "failed" && <p role="alert" className="mb-3">Could not save the decision</p>}
      {decision.status === "conflict" && (
        <p role="alert" className="mb-3">{CONFLICT_TEXT[decision.code] ?? "This proposal can no longer be applied"}</p>
      )}
      {body}
      {stats !== null && <div className="mt-6"><ReviewStats stats={stats} /></div>}
    </div>
  )
}
