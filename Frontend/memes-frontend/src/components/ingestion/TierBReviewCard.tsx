import type { MemesApi } from "../../api/MemesApi"
import type { IngestionClusterMember, IngestionTierBReviewItem } from "../../types/generated/all"
import type { Decision } from "./types"
import { MemberTile } from "./MemberTile"

type Props = {
  memesApi: MemesApi
  item: IngestionTierBReviewItem
  decisions: Record<string, Decision | undefined>
  onDecide: (imageId: string, d: Decision) => void
  onSubmit: () => void
  submitting: boolean
  expanded: boolean
  onToggleExpand: () => void
  onPeek: (member: IngestionClusterMember) => void
}

const COLLAPSED_COUNT = 8

export function TierBReviewCard({
  memesApi, item, decisions, onDecide, onSubmit, submitting, expanded, onToggleExpand, onPeek,
}: Props) {
  const decidable = [
    item.image.image_id,
    ...item.candidates.filter((c) => c.member.status === "pending").map((c) => c.member.image_id),
  ]
  const hasDecision = decidable.some((id) => decisions[id] !== undefined)
  const shown = expanded ? item.candidates : item.candidates.slice(0, COLLAPSED_COUNT)
  const hiddenInList = item.candidates.length - COLLAPSED_COUNT
  const cappedOff = item.total_candidates - item.candidates.length

  return (
    <div className="bg-white rounded-lg p-4 shadow-sm mb-4">
      <div className="mb-3 border-b pb-3">
        <MemberTile
          memesApi={memesApi} member={item.image} edgeSummary={null}
          decision={decisions[item.image.image_id]}
          onDecide={(d) => onDecide(item.image.image_id, d)}
          onPeek={() => onPeek(item.image)}
        />
      </div>
      <div className="grid gap-3 grid-cols-[repeat(auto-fill,minmax(300px,1fr))]">
        {shown.map((c) => (
          <MemberTile
            key={c.member.image_id}
            memesApi={memesApi}
            member={c.member}
            edgeSummary={`${c.distance.toFixed(3)} · ${c.match_source ?? "?"}`}
            decision={c.member.status === "pending" ? decisions[c.member.image_id] : undefined}
            onDecide={
              c.member.status === "pending"
                ? (d) => onDecide(c.member.image_id, d)
                : () => {}
            }
            onPeek={() => onPeek(c.member)}
          />
        ))}
      </div>
      {cappedOff > 0 && (
        <p className="mt-2 text-xs text-gray-500">
          Showing {item.candidates.length} of {item.total_candidates} candidates.
        </p>
      )}
      <div className="mt-3 flex items-center gap-3">
        <button
          className="text-sm rounded bg-blue-600 text-white px-3 py-1 transition-colors active:scale-[.97] disabled:opacity-40"
          disabled={!hasDecision || submitting}
          onClick={onSubmit}
        >
          {submitting ? "Submitting…" : "Submit decisions"}
        </button>
        {hiddenInList > 0 && (
          <button className="text-sm rounded bg-gray-100 hover:bg-gray-200 px-3 py-1" onClick={onToggleExpand}>
            {expanded ? "Show fewer" : `Show ${hiddenInList} more`}
          </button>
        )}
      </div>
    </div>
  )
}
