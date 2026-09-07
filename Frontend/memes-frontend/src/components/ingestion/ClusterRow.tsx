import type { MemesApi } from "../../api/MemesApi"
import type { IngestionCluster, IngestionClusterMember } from "../../types/generated/all"
import type { Decision } from "./types"
import { MemberTile } from "./MemberTile"

type Props = {
  memesApi: MemesApi
  cluster: IngestionCluster
  decisions: Record<string, Decision | undefined>
  onDecide: (imageId: string, d: Decision) => void
  onSubmit: () => void
  submitting: boolean
  onHoverPreview: (member: IngestionClusterMember) => void
  onLeavePreview: () => void
  onPeek: (member: IngestionClusterMember) => void
}

export function ClusterRow({
  memesApi, cluster, decisions, onDecide, onSubmit, submitting,
  onHoverPreview, onLeavePreview, onPeek,
}: Props) {
  const hasPendingDecision = cluster.members.some(
    (m) => m.status === "pending" && decisions[m.image_id] !== undefined
  )
  return (
    <div className="bg-white rounded-lg p-4 shadow-sm mb-4">
      <div className="flex gap-3 overflow-x-auto pb-2">
        {cluster.members.map((member) => {
          const edgeLabels = cluster.edges
            .filter((e) => e.image_id1 === member.image_id || e.image_id2 === member.image_id)
            .map((e) => `${e.distance.toFixed(3)} (${e.match_source ?? "?"})`)
          return (
            <MemberTile
              key={member.image_id}
              memesApi={memesApi}
              member={member}
              edgeLabels={edgeLabels}
              decision={decisions[member.image_id]}
              onDecide={(d) => onDecide(member.image_id, d)}
              onHoverPreview={() => onHoverPreview(member)}
              onLeavePreview={onLeavePreview}
              onPeek={() => onPeek(member)}
            />
          )
        })}
      </div>
      <button
        className="mt-3 text-sm rounded bg-blue-600 text-white px-3 py-1 transition-colors active:scale-[.97] disabled:opacity-40"
        disabled={!hasPendingDecision || submitting}
        onClick={onSubmit}
      >
        {submitting ? "Submitting…" : "Submit decisions"}
      </button>
    </div>
  )
}
