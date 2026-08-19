import { useCallback, useEffect, useRef, useState } from "react"
import type { MemesApi } from "../api/MemesApi"
import type { RunStatusResponse } from "../types/generated/all"
import DuplicateDecisionsPanel from "../components/DuplicateDecisionsPanel"

type Props = { memesApi: MemesApi }

const PAGE_SIZE = 20
const POLL_INTERVAL_MS = 4000
const CONFIRM_TIMEOUT_MS = 3000

const STATUS_COLOR: Record<string, string> = {
  running: "bg-blue-100 text-blue-800",
  completed: "bg-green-100 text-green-800",
  failed: "bg-red-100 text-red-800",
}

function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`text-xs px-2 py-0.5 rounded ${STATUS_COLOR[status] ?? "bg-gray-100 text-gray-800"}`}>
      {status}
    </span>
  )
}

function truncate(text: string | null, max: number): string {
  if (!text) return ""
  return text.length > max ? `${text.slice(0, max)}…` : text
}

function BatchRow({
  name, latestRun, pendingConfirm, triggering, triggerError, onRunClick,
}: {
  name: string
  latestRun: RunStatusResponse | undefined
  pendingConfirm: boolean
  triggering: boolean
  triggerError: string | undefined
  onRunClick: () => void
}) {
  const label = triggering ? "Triggering…" : pendingConfirm ? "Confirm?" : "Run"
  return (
    <div className="flex items-center gap-3 py-2 border-b last:border-0">
      <span className="font-medium w-56">{name}</span>
      {latestRun ? <StatusBadge status={latestRun.status} /> : <span className="text-xs text-gray-400">no recent run</span>}
      <button
        className={`ml-auto text-xs rounded px-3 py-1 disabled:opacity-50 ${pendingConfirm ? "bg-amber-500 text-white" : "bg-blue-600 text-white"}`}
        onClick={onRunClick}
        disabled={triggering}
      >
        {label}
      </button>
      {triggerError && <span className="text-xs text-red-500 ml-2">{triggerError}</span>}
    </div>
  )
}

export default function AdminBatchesPage({ memesApi }: Props) {
  const [batchNames, setBatchNames] = useState<string[]>([])
  const [namesLoading, setNamesLoading] = useState(true)
  const [runs, setRuns] = useState<RunStatusResponse[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [pendingConfirm, setPendingConfirm] = useState<string | null>(null)
  const [triggeringBatch, setTriggeringBatch] = useState<string | null>(null)
  const [triggerErrors, setTriggerErrors] = useState<Record<string, string>>({})
  const confirmTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const load = useCallback(() => {
    return memesApi.listBatchRuns(PAGE_SIZE, page * PAGE_SIZE)
      .then((res) => {
        setRuns(res.items)
        setTotal(res.total)
        setError(null)
        // A stale trigger error (e.g. a 409 from double-firing Run) should not sit
        // forever -- once a fresh load shows the batch actually running, whatever
        // conflict caused the error is over, so clear it.
        const runningBatches = res.items.filter((r) => r.status === "running").map((r) => r.batch_name)
        if (runningBatches.length > 0) {
          setTriggerErrors((prev) => {
            if (!runningBatches.some((name) => prev[name])) return prev
            const next = { ...prev }
            for (const name of runningBatches) delete next[name]
            return next
          })
        }
      })
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : "Failed to load batch runs")
      })
      .finally(() => setLoading(false))
  }, [memesApi, page])

  const loadNames = useCallback(() => {
    return memesApi.listBatchNames()
      .then((res) => setBatchNames(res.names))
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : "Failed to load batch names")
      })
      .finally(() => setNamesLoading(false))
  }, [memesApi])

  useEffect(() => {
    // `await Promise.resolve()` before the first setState defers it past the synchronous
    // effect body, per the react-hooks/set-state-in-effect rule (same pattern used in
    // TrendHistoryPage.tsx).
    void (async () => {
      await Promise.resolve()
      setLoading(true)
      load()
    })()
  }, [load])

  useEffect(() => {
    void (async () => {
      await Promise.resolve()
      setNamesLoading(true)
      loadNames()
    })()
  }, [loadNames])

  const hasRunningRun = runs.some((r) => r.status === "running")
  useEffect(() => {
    if (!hasRunningRun) return
    const id = setInterval(load, POLL_INTERVAL_MS)
    return () => clearInterval(id)
  }, [hasRunningRun, load])

  useEffect(() => {
    return () => {
      if (confirmTimeoutRef.current) clearTimeout(confirmTimeoutRef.current)
    }
  }, [])

  function handleRunClick(batchName: string) {
    if (pendingConfirm === batchName) {
      if (confirmTimeoutRef.current) clearTimeout(confirmTimeoutRef.current)
      setPendingConfirm(null)
      setTriggeringBatch(batchName)
      memesApi.triggerBatchRun(batchName)
        .then(() => {
          setTriggerErrors((prev) => ({ ...prev, [batchName]: "" }))
          load()
        })
        .catch((e: unknown) => {
          setTriggerErrors((prev) => ({
            ...prev,
            [batchName]: e instanceof Error ? e.message : `Failed to trigger ${batchName}`,
          }))
        })
        .finally(() => setTriggeringBatch(null))
      return
    }

    if (confirmTimeoutRef.current) clearTimeout(confirmTimeoutRef.current)
    setPendingConfirm(batchName)
    confirmTimeoutRef.current = setTimeout(() => setPendingConfirm(null), CONFIRM_TIMEOUT_MS)
  }

  function latestRunFor(batchName: string): RunStatusResponse | undefined {
    return runs.find((r) => r.batch_name === batchName)
  }

  if (loading || namesLoading) return (
    <div>
      <h1 className="text-2xl font-bold mb-4">Admin</h1>
      <p className="text-sm text-gray-400">Loading…</p>
    </div>
  )

  if (error) return (
    <div>
      <h1 className="text-2xl font-bold mb-4">Admin</h1>
      <p className="text-sm text-red-500">{error}</p>
    </div>
  )

  const maxPage = Math.max(0, Math.ceil(total / PAGE_SIZE) - 1)

  return (
    <div>
      <h1 className="text-2xl font-bold mb-4">Admin</h1>

      <div className="bg-white rounded-lg p-4 shadow-sm mb-6">
        {batchNames.map((name) => (
          <BatchRow
            key={name}
            name={name}
            latestRun={latestRunFor(name)}
            pendingConfirm={pendingConfirm === name}
            triggering={triggeringBatch === name}
            triggerError={triggerErrors[name] || undefined}
            onRunClick={() => handleRunClick(name)}
          />
        ))}
      </div>

      <DuplicateDecisionsPanel memesApi={memesApi} />

      <div className="bg-white rounded-lg p-4 shadow-sm">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-gray-500 border-b">
              <th className="py-1">Batch</th>
              <th>Trigger</th>
              <th>Status</th>
              <th>Created</th>
              <th>Completed</th>
              <th>Error</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.run_id} className="border-b last:border-0">
                <td className="py-1">{run.batch_name}</td>
                <td>{run.trigger}</td>
                <td><StatusBadge status={run.status} /></td>
                <td>{run.created_at}</td>
                <td>{run.completed_at ?? "—"}</td>
                <td title={run.error ?? undefined}>{truncate(run.error, 40)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {runs.length === 0 && <p className="text-sm text-gray-400 mt-2">No runs yet.</p>}

        <div className="flex items-center gap-3 mt-3">
          <button
            className="text-xs rounded bg-gray-100 px-3 py-1 disabled:opacity-40"
            disabled={page === 0}
            onClick={() => setPage((p) => Math.max(0, p - 1))}
          >
            Prev
          </button>
          <span className="text-xs text-gray-500">Page {page + 1} of {maxPage + 1}</span>
          <button
            className="text-xs rounded bg-gray-100 px-3 py-1 disabled:opacity-40"
            disabled={page >= maxPage}
            onClick={() => setPage((p) => p + 1)}
          >
            Next
          </button>
        </div>
      </div>
    </div>
  )
}
