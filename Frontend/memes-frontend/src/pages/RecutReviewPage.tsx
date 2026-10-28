import { useCallback, useEffect } from "react"
import { useSearchParams } from "react-router-dom"
import type { MemesApi } from "../api/MemesApi"
import BeforeAfter from "../components/recut/BeforeAfter"
import ProposalInfo from "../components/recut/ProposalInfo"
import QueueStrip from "../components/recut/QueueStrip"
import { useRecutQueue } from "../recut/useRecutQueue"

type Props = { memesApi: MemesApi }

const LOAD_AHEAD = 5

export default function RecutReviewPage({ memesApi }: Props) {
  const [params, setParams] = useSearchParams()
  const openId = params.get("cut")
  const queue = useRecutQueue(memesApi, openId)
  const { items, total, listStatus, loadMore, detail } = queue

  const open = useCallback((id: string, replace = false) => setParams({ cut: id }, { replace }), [setParams])

  const firstId = items[0]?.id ?? null
  const detailStatus = detail?.status
  useEffect(() => {
    if (listStatus !== "ready" || firstId === null || firstId === openId) return
    if (openId === null || detailStatus === "missing") open(firstId, true)
  }, [listStatus, firstId, openId, detailStatus, open])

  const position = items.findIndex((item) => item.id === openId)
  useEffect(() => {
    if (position >= 0 && position >= items.length - LOAD_AHEAD) loadMore()
  }, [position, items.length, loadMore])

  const previousId = detail?.status === "ready" ? detail.data.previousId : null
  const nextId = detail?.status === "ready" ? detail.data.nextId : null
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return  // Alt+arrow is the browser's back/forward
      const target = event.target as HTMLElement | null
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable)) return
      if (event.key === "ArrowLeft" && previousId !== null) open(previousId)
      if (event.key === "ArrowRight" && nextId !== null) open(nextId)
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [previousId, nextId, open])

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
          key={proposal.id}
          sourceUrl={memesApi.getImageUrlById(proposal.sourceImageId)}
          cutUrl={memesApi.getImageUrlById(proposal.cutImageId)}
          box={proposal.box}
        />
        <div className="mt-4"><ProposalInfo detail={proposal} /></div>
        <div className="mt-4 flex items-center gap-3">
          <button type="button" disabled className="min-h-11 rounded border px-3 opacity-50">Approve</button>
          <button type="button" disabled className="min-h-11 rounded border px-3 opacity-50">Reject</button>
          <span className="text-xs text-gray-500">Deciding arrives with the approve/reject step</span>
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
      {body}
    </div>
  )
}
