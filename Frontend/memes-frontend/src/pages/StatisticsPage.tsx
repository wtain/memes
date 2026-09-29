import { useEffect, useRef, useState } from "react"
import type { MemesApi } from "../api/MemesApi"
import type { StatisticsResponse } from "../types/generated/all"
import { formatUpdatedAgo } from "./formatUpdatedAgo"
import { buildSections, changedCellLabels, type StatCell } from "./statisticsSections"
import { useTweenedStats } from "./useTweenedStats"

type Props = { memesApi: MemesApi }

const FLASH_MS = 1500

function StatGrid({ cells, flashing }: { cells: StatCell[]; flashing: ReadonlySet<string> }) {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
      {cells.map(({ label, value }) => (
        <div
          key={label}
          className={`rounded-lg p-4 shadow-sm transition-colors duration-1000 ${flashing.has(label) ? "bg-amber-100" : "bg-white"}`}
        >
          <div className="text-sm text-gray-500">{label}</div>
          <div className="text-xl font-semibold mt-1">{value}</div>
        </div>
      ))}
    </div>
  )
}

export default function StatisticsPage({ memesApi }: Props) {
  const [snapshot, setSnapshot] = useState<StatisticsResponse | null>(null)
  const [live, setLive] = useState<StatisticsResponse | null>(null)
  const [liveSettled, setLiveSettled] = useState(false)
  const [snapshotError, setSnapshotError] = useState<string | null>(null)
  const [flashing, setFlashing] = useState<ReadonlySet<string>>(new Set())
  const loadedRef = useRef(false)
  const snapshotRef = useRef<StatisticsResponse | null>(null)
  const flashTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    if (loadedRef.current) return
    loadedRef.current = true

    memesApi.getStatistics()
      .then((result) => {
        snapshotRef.current = result
        setSnapshot(result)
      })
      .catch((e: unknown) => setSnapshotError(e instanceof Error ? e.message : "Failed to load statistics"))

    // Live failures are deliberately silent: the snapshot (if any) stays on screen.
    memesApi.getStatistics({ live: true })
      .then((result) => {
        setLive(result)
        if (snapshotRef.current) {
          setFlashing(changedCellLabels(snapshotRef.current, result))
          if (flashTimerRef.current) clearTimeout(flashTimerRef.current)
          flashTimerRef.current = setTimeout(() => setFlashing(new Set()), FLASH_MS)
        }
      })
      .catch(() => {})
      .finally(() => setLiveSettled(true))
  }, [memesApi])

  useEffect(() => () => {
    if (flashTimerRef.current) clearTimeout(flashTimerRef.current)
  }, [])

  const stats = useTweenedStats(live ?? snapshot)

  if (snapshotError && liveSettled && !live) return (
    <div className="space-y-8">
      <h1 className="text-2xl font-bold mb-4">Statistics</h1>
      <p className="text-sm text-red-500">{snapshotError}</p>
    </div>
  )

  if (!stats) return (
    <div className="space-y-8">
      <h1 className="text-2xl font-bold mb-4">Statistics</h1>
      <p className="text-sm text-gray-400">Loading…</p>
    </div>
  )

  const updatedAgo = formatUpdatedAgo(stats.computed_at)
  const refreshing = snapshot !== null && !liveSettled

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold mb-1">Statistics</h1>
        <div className="flex items-center gap-3">
          {updatedAgo && <p className="text-sm text-gray-500">{updatedAgo}</p>}
          {refreshing && (
            <span role="status" className="inline-flex items-center gap-1.5 text-sm text-gray-400">
              <span className="h-2 w-2 rounded-full bg-gray-400 animate-pulse" aria-hidden="true" />
              Refreshing…
            </span>
          )}
        </div>
      </div>

      {buildSections(stats).map((section) => (
        <section key={section.title}>
          <h2 className="text-lg font-semibold mb-3">{section.title}</h2>
          <StatGrid cells={section.cells} flashing={flashing} />
        </section>
      ))}
    </div>
  )
}
