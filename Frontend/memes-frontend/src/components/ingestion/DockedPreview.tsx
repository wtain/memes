import type { MemesApi } from "../../api/MemesApi"
import type { IngestionClusterMember } from "../../types/generated/all"
import type { Decision } from "./types"

type Props = {
  memesApi: MemesApi
  member: IngestionClusterMember | null
  decision: Decision | undefined
  onDecide: (d: Decision) => void
  onMouseEnter: () => void
  onMouseLeave: () => void
}

export function DockedPreview({ memesApi, member, decision, onDecide, onMouseEnter, onMouseLeave }: Props) {
  if (!member) return null
  return (
    <aside
      className="hidden lg:flex flex-col fixed top-0 right-0 h-screen w-[40vw] max-w-[720px] z-40 bg-white/98 border-l shadow-2xl p-4 gap-3"
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
    >
      <div className="text-xs text-gray-500 truncate" title={member.filename}>{member.filename}</div>
      <div className="flex-1 overflow-auto flex items-start justify-center">
        <img
          src={memesApi.getImageUrlById(member.image_id)}
          alt={member.filename}
          decoding="async"
          className="max-w-full"
        />
      </div>
      {member.ocr_text && (
        <div className="text-sm text-gray-800 max-h-48 overflow-y-auto whitespace-pre-wrap break-words shrink-0">
          {member.ocr_text}
        </div>
      )}
      {member.status === "pending" && (
        <div className="flex gap-2 shrink-0">
          <button
            className={`flex-1 rounded px-3 py-2 text-sm transition-colors active:scale-[.97] ${decision === "keep" ? "bg-green-600 text-white" : "bg-gray-100 hover:bg-gray-200"}`}
            onClick={() => onDecide("keep")}
          >Keep</button>
          <button
            className={`flex-1 rounded px-3 py-2 text-sm transition-colors active:scale-[.97] ${decision === "reject" ? "bg-red-600 text-white" : "bg-gray-100 hover:bg-gray-200"}`}
            onClick={() => onDecide("reject")}
          >Reject</button>
        </div>
      )}
    </aside>
  )
}
