import type { MemesApi } from "../../api/MemesApi"
import type { IngestionClusterMember } from "../../types/generated/all"
import type { Decision } from "./types"

type Props = {
  memesApi: MemesApi
  member: IngestionClusterMember
  edgeSummary: string | null
  decision: Decision | undefined
  onDecide: (d: Decision) => void
  onPeek: () => void
  // A capped cluster is context-only: a Keep/Reject on a shown member would settle its
  // candidate pairs against the capped-off members nobody saw (via mark_reviewed), so the
  // decision controls are withheld until per-image tier B review ships.
  readOnly?: boolean
}

const BTN = "flex-1 text-xs rounded px-2 py-1 transition-colors active:scale-[.97] active:brightness-95"

export function MemberTile({ memesApi, member, edgeSummary, decision, onDecide, onPeek, readOnly }: Props) {
  const isPending = member.status === "pending"
  return (
    <div className={`border rounded-lg p-2 ${decision === "reject" ? "opacity-40" : ""}`}>
      {/* A real <button> wraps the image: correct Enter/Space semantics for the click-to-zoom
          peek, and the <img alt> stays the accessible name. */}
      <button
        type="button"
        aria-label={`Open ${member.filename} full size`}
        className="block w-full cursor-zoom-in"
        onClick={onPeek}
      >
        <img
          src={memesApi.getImageUrlById(member.image_id)}
          alt={member.filename}
          loading="lazy"
          decoding="async"
          className="w-full max-h-[420px] object-contain rounded bg-gray-50"
        />
      </button>
      <div className="text-xs mt-1 truncate" title={member.filename}>{member.filename}</div>
      <div className="text-xs">
        <span className={isPending ? "text-blue-600" : "text-gray-400"}>{member.status}</span>
      </div>
      {member.ocr_text && (
        <div className="text-[13px] text-gray-700 mt-1 max-h-40 overflow-y-auto whitespace-pre-wrap break-words border-l-2 border-gray-200 pl-2">
          {member.ocr_text}
        </div>
      )}
      {edgeSummary && <div className="text-[11px] text-gray-500 mt-0.5">{edgeSummary}</div>}
      {isPending && !readOnly && (
        <div className="flex gap-1 mt-2">
          <button
            className={`${BTN} ${decision === "keep" ? "bg-green-600 text-white" : "bg-gray-100 hover:bg-gray-200"}`}
            onClick={() => onDecide("keep")}
          >Keep</button>
          <button
            className={`${BTN} ${decision === "reject" ? "bg-red-600 text-white" : "bg-gray-100 hover:bg-gray-200"}`}
            onClick={() => onDecide("reject")}
          >Reject</button>
        </div>
      )}
    </div>
  )
}
