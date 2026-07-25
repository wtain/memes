import { useCallback, useEffect, useRef, useState } from "react"
import type { MemesApi, IngestionTier } from "../api/MemesApi"
import type { IngestionCluster, IngestionRunStatus } from "../types/generated/all"

type Props = { memesApi: MemesApi }

type Decision = "reject" | "keep"

// Which tier's queue to show, driven by the run's current stage -- promotion isn't
// implemented yet, so "promoted" falls back to tier_b's (empty, by then) queue rather than
// a dedicated "done" view.
function tierForStage(stage: string | null): IngestionTier | null {
  if (stage === "tier_a_review") return "tier_a"
  if (stage === "tier_b_review" || stage === "promoted") return "tier_b"
  return null // hash_dedup (candidates not computed yet) or ocr_prepass (transient)
}

const TIER_LABEL: Record<IngestionTier, string> = { tier_a: "Tier A", tier_b: "Tier B" }

function StatusBanner({ status }: { status: IngestionRunStatus | null }) {
  if (!status) return null
  const stats = status.stats ?? {}
  return (
    <div className="bg-white rounded-lg p-4 shadow-sm mb-6">
      <div className="flex items-center gap-3">
        <span className="text-sm text-gray-500">Run</span>
        <span className="font-mono text-xs text-gray-700">{status.run_id}</span>
        <span className="text-sm text-gray-500 ml-4">Stage</span>
        <span className="font-semibold">{status.stage}</span>
      </div>
      <div className="mt-2 flex gap-4 text-sm text-gray-600">
        {Object.entries(stats).map(([key, value]) => (
          <span key={key}>{key}: <span className="font-semibold">{String(value)}</span></span>
        ))}
      </div>
    </div>
  )
}

function MemberTile({
  memesApi, memberId, filename, memberStatus, ocrText, edgeLabels, decision, onDecide,
}: {
  memesApi: MemesApi
  memberId: string
  filename: string
  memberStatus: string
  ocrText: string | null
  edgeLabels: string[]
  decision: Decision | undefined
  onDecide: (decision: Decision) => void
}) {
  const isPending = memberStatus === "pending"
  return (
    <div className={`border rounded-lg p-2 w-48 ${decision === "reject" ? "opacity-40" : ""}`}>
      <img
        src={memesApi.getImageUrlById(memberId)}
        alt={filename}
        className="w-full h-32 object-cover rounded"
      />
      <div className="text-xs mt-1 truncate" title={filename}>{filename}</div>
      <div className="text-xs">
        <span className={isPending ? "text-blue-600" : "text-gray-400"}>{memberStatus}</span>
      </div>
      {ocrText && (
        <div className="text-[11px] text-gray-700 mt-1 line-clamp-3" title={ocrText}>
          “{ocrText}”
        </div>
      )}
      {edgeLabels.map((label) => (
        <div key={label} className="text-[11px] text-gray-500">{label}</div>
      ))}
      {isPending && (
        <div className="flex gap-1 mt-2">
          <button
            className={`flex-1 text-xs rounded px-1 py-1 ${decision === "keep" ? "bg-green-600 text-white" : "bg-gray-100"}`}
            onClick={() => onDecide("keep")}
          >
            Keep
          </button>
          <button
            className={`flex-1 text-xs rounded px-1 py-1 ${decision === "reject" ? "bg-red-600 text-white" : "bg-gray-100"}`}
            onClick={() => onDecide("reject")}
          >
            Reject
          </button>
        </div>
      )}
    </div>
  )
}

export default function IngestionReviewPage({ memesApi }: Props) {
  const [status, setStatus] = useState<IngestionRunStatus | null>(null)
  const [clusters, setClusters] = useState<IngestionCluster[]>([])
  const [decisions, setDecisions] = useState<Record<string, Decision | undefined>>({})
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState<number | null>(null)
  const tier = status ? tierForStage(status.stage) : null

  const load = useCallback(() => {
    return memesApi.getIngestionRunStatus()
      .then((s) => {
        setStatus(s)
        setError(null)
        const tier = s ? tierForStage(s.stage) : null
        return tier ? memesApi.getIngestionClusters(tier) : []
      })
      .then(setClusters)
      .catch((e: unknown) => {
        setStatus(null)
        setClusters([])
        setError(e instanceof Error ? e.message : "Failed to load ingestion review")
      })
      .finally(() => setLoading(false))
  }, [memesApi])

  const loadedRef = useRef(false)
  useEffect(() => {
    if (loadedRef.current) return
    loadedRef.current = true
    load()
  }, [load])

  function setDecision(memberId: string, decision: Decision) {
    setDecisions((prev) => ({ ...prev, [memberId]: prev[memberId] === decision ? undefined : decision }))
  }

  async function submitCluster(clusterIndex: number, cluster: IngestionCluster) {
    const memberIds = new Set(cluster.members.map((m) => m.image_id))
    const clusterDecisions: { image_id: string; decision: Decision }[] = []
    for (const [image_id, decision] of Object.entries(decisions)) {
      if (memberIds.has(image_id) && decision !== undefined) {
        clusterDecisions.push({ image_id, decision })
      }
    }

    if (clusterDecisions.length === 0 || !tier) return

    setSubmitting(clusterIndex)
    try {
      await memesApi.resolveIngestionCluster(tier, clusterDecisions)
      load()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to submit decisions")
    } finally {
      setSubmitting(null)
    }
  }

  if (loading) return (
    <div>
      <h1 className="text-2xl font-bold mb-4">Ingestion Review</h1>
      <p className="text-sm text-gray-400">Loading…</p>
    </div>
  )

  if (error) return (
    <div>
      <h1 className="text-2xl font-bold mb-4">Ingestion Review</h1>
      <p className="text-sm text-red-500">{error}</p>
    </div>
  )

  if (!status) return (
    <div>
      <h1 className="text-2xl font-bold mb-4">Ingestion Review</h1>
      <p className="text-sm text-gray-400">No ingestion run is currently in progress.</p>
    </div>
  )

  return (
    <div>
      <h1 className="text-2xl font-bold mb-4">
        Ingestion Review{tier ? ` — ${TIER_LABEL[tier]}` : ""}
      </h1>
      <StatusBanner status={status} />

      {!tier && (
        <p className="text-sm text-gray-400">
          {status.stage === "ocr_prepass"
            ? "OCR pre-pass is running — Tier B review will be available once it finishes."
            : "Candidates haven't been computed for this run yet."}
        </p>
      )}

      {tier && clusters.length === 0 && (
        <p className="text-sm text-gray-400">No {TIER_LABEL[tier]} clusters need review right now.</p>
      )}

      <div className="space-y-4">
        {clusters.map((cluster, i) => {
          const memberIds = cluster.members.map((m) => m.image_id)
          const hasPendingDecision = memberIds.some((id) => decisions[id] !== undefined)

          return (
            <div key={i} className="bg-white rounded-lg p-4 shadow-sm">
              <div className="flex flex-wrap gap-3">
                {cluster.members.map((member) => {
                  const edgeLabels = cluster.edges
                    .filter((e) => e.image_id1 === member.image_id || e.image_id2 === member.image_id)
                    .map((e) => `${e.distance.toFixed(3)} (${e.match_source ?? "?"})`)
                  return (
                    <MemberTile
                      key={member.image_id}
                      memesApi={memesApi}
                      memberId={member.image_id}
                      filename={member.filename}
                      memberStatus={member.status}
                      ocrText={member.ocr_text}
                      edgeLabels={edgeLabels}
                      decision={decisions[member.image_id]}
                      onDecide={(d) => setDecision(member.image_id, d)}
                    />
                  )
                })}
              </div>
              <button
                className="mt-3 text-sm rounded bg-blue-600 text-white px-3 py-1 disabled:opacity-40"
                disabled={!hasPendingDecision || submitting === i}
                onClick={() => submitCluster(i, cluster)}
              >
                {submitting === i ? "Submitting…" : "Submit decisions"}
              </button>
            </div>
          )
        })}
      </div>
    </div>
  )
}
