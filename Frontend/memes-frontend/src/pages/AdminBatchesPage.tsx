import { useCallback, useEffect, useRef, useState } from "react"
import type { MemesApi } from "../api/MemesApi"
import type { RunStatusResponse } from "../types/generated/all"
import BusinessMetricsPanel from "../components/BusinessMetricsPanel"
import DuplicateDecisionsPanel from "../components/DuplicateDecisionsPanel"
import { describeProgress, tableFigure } from "./batchProgress"
import { formatAbsolute, formatDuration, formatRelative, runDurationMs } from "./batchTime"

type Props = { memesApi: MemesApi }

const INGEST_NOTE =
  "Reject share, not precision: a kept image can be the winner of a true duplicate group. Pending images are not counted as kept."

const LIBRARY_NOTE =
  "Counts images in decided groups, not detector precision: a confirmed group is its flagged copies plus the keeper. Each image counts once per outcome, under the clusterize run that was current at its first decision."

const PAGE_SIZE = 20
const POLL_INTERVAL_MS = 4000
const CONFIRM_TIMEOUT_MS = 3000

// The values GET /api/admin/batches/runs?status= accepts (one per stored run status; a backend test pins them).
const RUN_STATUSES = ["running", "completed", "failed", "aborted"]
const NAME_FILTER_DEBOUNCE_MS = 300

const STATUS_COLOR: Record<string, string> = {
  running: "bg-blue-100 text-blue-800",
  completed: "bg-green-100 text-green-800",
  failed: "bg-red-100 text-red-800",
  aborted: "bg-amber-100 text-amber-800",
}

function Timestamp({ iso, now }: { iso: string; now: number }) {
  return <time dateTime={iso} title={formatAbsolute(iso)}>{formatRelative(iso, now)}</time>
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
  name, latestRun, now, pendingConfirm, triggering, triggerError, onRunClick,
}: {
  name: string
  latestRun: RunStatusResponse | undefined
  now: number
  pendingConfirm: boolean
  triggering: boolean
  triggerError: string | undefined
  onRunClick: () => void
}) {
  const view = latestRun?.status === "running" && latestRun.progress
    ? describeProgress(latestRun.progress, now)
    : null
  const label = triggering ? "Triggering…" : pendingConfirm ? "Confirm?" : "Run"
  return (
    <div className="py-2 border-b last:border-0">
      <div className="flex items-center gap-3">
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
      {view && (
        <div className="mt-1 text-xs text-gray-600 space-y-1">
          {view.bar !== null && (
            <div
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round(view.bar * 100)}
              className="h-2 w-64 rounded bg-gray-200 overflow-hidden"
            >
              <div className="h-full bg-blue-500" style={{ width: `${view.bar * 100}%` }} />
            </div>
          )}
          <div className="flex flex-wrap gap-x-3">
            {view.counts && <span>{view.counts}</span>}
            {view.eta && <span>{view.eta}</span>}
            {view.step && <span>{view.step}</span>}
            {view.updated && (
              <span className={view.stalled ? "text-amber-600" : "text-gray-400"}>
                {view.updated}
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

export default function AdminBatchesPage({ memesApi }: Props) {
  const [batchNames, setBatchNames] = useState<string[]>([])
  const [namesLoading, setNamesLoading] = useState(true)
  const [runs, setRuns] = useState<RunStatusResponse[]>([])
  // The newest runs, unfiltered and unpaged: what the batch rows above the history show as "latest run".
  const [latest, setLatest] = useState<RunStatusResponse[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(0)
  const [batchFilter, setBatchFilter] = useState("")
  const [statusFilter, setStatusFilter] = useState("")
  const [runNameInput, setRunNameInput] = useState("")
  const [runName, setRunName] = useState("")
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [pendingConfirm, setPendingConfirm] = useState<string | null>(null)
  const [triggeringBatch, setTriggeringBatch] = useState<string | null>(null)
  const [triggerErrors, setTriggerErrors] = useState<Record<string, string>>({})
  const [now, setNow] = useState(() => Date.now())
  const confirmTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const load = useCallback(() => {
    const filters = {
      ...(statusFilter ? { status: statusFilter } : {}),
      ...(runName ? { name: runName } : {}),
    }
    const filtered = Object.keys(filters).length > 0
    const table = filtered
      ? memesApi.listBatchRuns(PAGE_SIZE, page * PAGE_SIZE, filters)
      : memesApi.listBatchRuns(PAGE_SIZE, page * PAGE_SIZE)
    // The unfiltered first page doubles as the latest runs; any other view needs its own request.
    const newest = filtered || page > 0 ? memesApi.listBatchRuns(PAGE_SIZE, 0) : table
    return Promise.all([table, newest])
      .then(([res, newestRes]) => {
        setRuns(res.items)
        setTotal(res.total)
        setLatest(newestRes.items)
        setError(null)
        // A stale trigger error (e.g. a 409 from double-firing Run) should not sit
        // forever -- once a fresh load shows the batch actually running, whatever
        // conflict caused the error is over, so clear it.
        const runningBatches = newestRes.items.filter((r) => r.status === "running").map((r) => r.batch_name)
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
  }, [memesApi, page, statusFilter, runName])

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
    // `loading` only covers the very first load: a filter change keeps the page (and the focused input) in place.
    void (async () => {
      await Promise.resolve()
      load()
    })()
  }, [load])

  useEffect(() => {
    const id = setTimeout(() => {
      setRunName(runNameInput.trim())
      setPage(0)
    }, NAME_FILTER_DEBOUNCE_MS)
    return () => clearTimeout(id)
  }, [runNameInput])

  useEffect(() => {
    void (async () => {
      await Promise.resolve()
      setNamesLoading(true)
      loadNames()
    })()
  }, [loadNames])

  const hasRunningRun = runs.some((r) => r.status === "running") || latest.some((r) => r.status === "running")
  useEffect(() => {
    if (!hasRunningRun) return
    const id = setInterval(load, POLL_INTERVAL_MS)
    return () => clearInterval(id)
  }, [hasRunningRun, load])

  // One-second tick so "updated 20 s ago" and a running run's duration move, only while one is running.
  useEffect(() => {
    if (!hasRunningRun) return
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [hasRunningRun])

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
    return latest.find((r) => r.batch_name === batchName)
  }

  function chooseStatus(value: string) {
    setStatusFilter(value)
    setPage(0)
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
  const batchNeedle = batchFilter.trim().toLowerCase()
  const shownBatchNames = batchNames.filter((name) => name.toLowerCase().includes(batchNeedle))
  const runsFiltered = statusFilter !== "" || runName !== ""

  return (
    <div>
      <h1 className="text-2xl font-bold mb-4">Admin</h1>

      <div className="bg-white rounded-lg p-4 shadow-sm mb-6">
        <input
          type="search"
          aria-label="Filter batches"
          placeholder="Filter batches…"
          value={batchFilter}
          onChange={(e) => setBatchFilter(e.target.value)}
          className="border rounded px-2 py-1 text-sm w-64 mb-2"
        />
        {shownBatchNames.length === 0 && <p className="text-sm text-gray-400">No batch matches.</p>}
        {shownBatchNames.map((name) => (
          <BatchRow
            key={name}
            name={name}
            latestRun={latestRunFor(name)}
            now={now}
            pendingConfirm={pendingConfirm === name}
            triggering={triggeringBatch === name}
            triggerError={triggerErrors[name] || undefined}
            onRunClick={() => handleRunClick(name)}
          />
        ))}
      </div>

      <div className="bg-white rounded-lg p-4 shadow-sm mb-6">
        <div className="flex items-center gap-3 mb-3">
          <input
            type="search"
            aria-label="Filter runs by batch name"
            placeholder="Filter runs by batch name…"
            value={runNameInput}
            onChange={(e) => setRunNameInput(e.target.value)}
            className="border rounded px-2 py-1 text-sm w-64"
          />
          <label className="text-sm text-gray-600 flex items-center gap-2">
            Status
            <select
              value={statusFilter}
              onChange={(e) => chooseStatus(e.target.value)}
              className="border rounded px-2 py-1 text-sm"
            >
              <option value="">All statuses</option>
              {RUN_STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </label>
        </div>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-gray-500 border-b">
              <th className="py-1">Batch</th>
              <th>Trigger</th>
              <th>Status</th>
              <th>Progress</th>
              <th>Created</th>
              <th>Completed</th>
              <th>Duration</th>
              <th>Error</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.run_id} className="border-b last:border-0">
                <td className="py-1">{run.batch_name}</td>
                <td>{run.trigger}</td>
                <td><StatusBadge status={run.status} /></td>
                <td>{tableFigure(run.progress)}</td>
                <td><Timestamp iso={run.created_at} now={now} /></td>
                <td>{run.completed_at ? <Timestamp iso={run.completed_at} now={now} /> : "—"}</td>
                <td>{(() => {
                  const ms = runDurationMs(run, now)
                  return ms === null ? "—" : formatDuration(ms)
                })()}</td>
                <td title={run.error ?? undefined}>{truncate(run.error, 40)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {runs.length === 0 && (
          <p className="text-sm text-gray-400 mt-2">{runsFiltered ? "No runs match." : "No runs yet."}</p>
        )}

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

      <BusinessMetricsPanel
        memesApi={memesApi}
        process="recut"
        title="Recut outcomes"
        emptyText="No recut proposals yet"
      />
      <BusinessMetricsPanel
        memesApi={memesApi}
        process="ingest_tier_a"
        title="Ingestion duplicates: Tier A"
        emptyText="No tier A duplicate candidates yet"
        note={INGEST_NOTE}
      />
      <BusinessMetricsPanel
        memesApi={memesApi}
        process="ingest_tier_b"
        title="Ingestion duplicates: Tier B"
        emptyText="No tier B duplicate candidates yet"
        note={INGEST_NOTE}
      />
      <BusinessMetricsPanel
        memesApi={memesApi}
        process="library_duplicates"
        title="Library duplicates"
        emptyText="No library duplicate decisions yet"
        note={LIBRARY_NOTE}
      />

      <DuplicateDecisionsPanel memesApi={memesApi} />
    </div>
  )
}
