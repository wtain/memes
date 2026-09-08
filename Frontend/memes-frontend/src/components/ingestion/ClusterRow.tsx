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
  expanded: boolean
  onToggleExpand: () => void
  onPeek: (member: IngestionClusterMember) => void
}

// A large near-duplicate cluster is usually one template repeated -- a reviewer decides it
// after a handful of tiles. Show this many, then collapse the rest behind a button.
const COLLAPSED_COUNT = 8

function edgeSummaryFor(cluster: IngestionCluster, imageId: string): string | null {
  const dists = cluster.edges
    .filter((e) => e.image_id1 === imageId || e.image_id2 === imageId)
    .map((e) => e.distance)
  if (dists.length === 0) return null
  const nearest = Math.min(...dists).toFixed(3)
  return `${nearest} nearest · ${dists.length} pair${dists.length === 1 ? "" : "s"}`
}

export function ClusterRow({
  memesApi, cluster, decisions, onDecide, onSubmit, submitting, expanded, onToggleExpand, onPeek,
}: Props) {
  const hasPendingDecision = cluster.members.some(
    (m) => m.status === "pending" && decisions[m.image_id] !== undefined
  )
  const hidden = cluster.members.length - COLLAPSED_COUNT
  const shown = expanded ? cluster.members : cluster.members.slice(0, COLLAPSED_COUNT)

  return (
    <div className="bg-white rounded-lg p-4 shadow-sm mb-4">
      <div className="grid gap-3 grid-cols-[repeat(auto-fill,minmax(300px,1fr))]">
        {shown.map((member) => (
          <MemberTile
            key={member.image_id}
            memesApi={memesApi}
            member={member}
            edgeSummary={edgeSummaryFor(cluster, member.image_id)}
            decision={decisions[member.image_id]}
            onDecide={(d) => onDecide(member.image_id, d)}
            onPeek={() => onPeek(member)}
          />
        ))}
      </div>
      <div className="mt-3 flex items-center gap-3">
        <button
          className="text-sm rounded bg-blue-600 text-white px-3 py-1 transition-colors active:scale-[.97] disabled:opacity-40"
          disabled={!hasPendingDecision || submitting}
          onClick={onSubmit}
        >
          {submitting ? "Submitting…" : "Submit decisions"}
        </button>
        {hidden > 0 && (
          <button
            className="text-sm rounded bg-gray-100 hover:bg-gray-200 px-3 py-1"
            onClick={onToggleExpand}
          >
            {expanded ? `Show fewer` : `Show ${hidden} more`}
          </button>
        )}
      </div>
    </div>
  )
}
