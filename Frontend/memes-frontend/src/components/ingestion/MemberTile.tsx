import type { MemesApi } from "../../api/MemesApi"
import type { IngestionClusterMember } from "../../types/generated/all"
import type { Decision } from "./types"

type Props = {
  memesApi: MemesApi
  member: IngestionClusterMember
  edgeLabels: string[]
  decision: Decision | undefined
  onDecide: (d: Decision) => void
  onHoverPreview: () => void
  onLeavePreview: () => void
  onPeek: () => void
}

const BTN = "flex-1 text-xs rounded px-2 py-1 transition-colors active:scale-[.97] active:brightness-95"

export function MemberTile({
  memesApi, member, edgeLabels, decision, onDecide, onHoverPreview, onLeavePreview, onPeek,
}: Props) {
  const isPending = member.status === "pending"
  return (
    <div className={`shrink-0 w-[22rem] max-w-[80vw] border rounded-lg p-2 ${decision === "reject" ? "opacity-40" : ""}`}>
      {/*
        Preview open/close and the peek shortcut live on this button, not the tile container. On
        the container they misfired: pointer/focus events bubble up from the inner Keep/Reject
        buttons (React synthesizes onMouseEnter/onFocus for every ancestor with the handler), so
        hovering or clicking a button, or tabbing between the two, flapped the docked preview
        open/closed and Enter on a button fired both onDecide and onPeek. A real <button> also
        gives correct keyboard semantics (Enter + Space) for free and keeps the <img> a plain
        image (its alt is the accessible name).
      */}
      <button
        type="button"
        aria-label={`Open ${member.filename} full size`}
        className="block w-full cursor-zoom-in"
        onClick={onPeek}
        onMouseEnter={onHoverPreview}
        onMouseLeave={onLeavePreview}
        onFocus={onHoverPreview}
        onBlur={onLeavePreview}
      >
        <img
          src={memesApi.getImageUrlById(member.image_id)}
          alt={member.filename}
          loading="lazy"
          decoding="async"
          className="w-full max-h-[55vh] object-contain rounded bg-gray-50"
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
      {edgeLabels.map((label) => (
        <div key={label} className="text-[11px] text-gray-500 mt-0.5">{label}</div>
      ))}
      {isPending && (
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
