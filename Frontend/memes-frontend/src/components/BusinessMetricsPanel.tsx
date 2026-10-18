import { useEffect, useState } from "react"
import type { MemesApi } from "../api/MemesApi"
import type { BatchMetricsBucket, BatchMetricsResponse } from "../types/generated/all"

type Props = { memesApi: MemesApi; process: string; title: string; emptyText: string }

type State =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: BatchMetricsResponse }

function label(key: string): string {
  const spaced = key.replace(/_/g, " ")
  return spaced.charAt(0).toUpperCase() + spaced.slice(1)
}

// A null metric means its denominator was zero. Names ending in _rate or _share are fractions.
function formatMetric(key: string, value: number | null): string {
  if (value === null) return "-"
  return /_(rate|share)$/.test(key) ? `${(value * 100).toFixed(1)}%` : String(value)
}

function Cells({ bucket, countKeys, metricKeys }: { bucket: BatchMetricsBucket; countKeys: string[]; metricKeys: string[] }) {
  return (
    <>
      {countKeys.map((key) => <td key={`c-${key}`}>{bucket.counts[key] ?? 0}</td>)}
      {metricKeys.map((key) => <td key={`m-${key}`}>{formatMetric(key, bucket.metrics[key] ?? null)}</td>)}
    </>
  )
}

export default function BusinessMetricsPanel({ memesApi, process, title, emptyText }: Props) {
  const [state, setState] = useState<State>({ status: "loading" })

  useEffect(() => {
    let cancelled = false
    // `await Promise.resolve()` keeps the first setState out of the synchronous effect body, same as
    // AdminBatchesPage.tsx (react-hooks/set-state-in-effect).
    void (async () => {
      await Promise.resolve()
      try {
        const data = await memesApi.getBatchMetrics(process)
        if (!cancelled) setState({ status: "ready", data })
      } catch (e: unknown) {
        if (!cancelled) setState({ status: "error", message: e instanceof Error ? e.message : "Failed to load metrics" })
      }
    })()
    return () => { cancelled = true }
  }, [memesApi, process])

  return (
    <div className="bg-white rounded-lg p-4 shadow-sm mb-6">
      <h2 className="text-lg font-semibold mb-2">{title}</h2>
      <Body state={state} emptyText={emptyText} />
    </div>
  )
}

function Body({ state, emptyText }: { state: State; emptyText: string }) {
  if (state.status === "loading") return <p className="text-sm text-gray-400">Loading…</p>
  if (state.status === "error") return <p className="text-sm text-red-500">{state.message}</p>

  const { items } = state.data
  const unattributed = Object.values(state.data.unattributed.counts).some((n) => n > 0) ? state.data.unattributed : null
  const sample = items[0] ?? unattributed
  if (!sample) return <p className="text-sm text-gray-400">{emptyText}</p>
  const countKeys = Object.keys(sample.counts)
  const metricKeys = Object.keys(sample.metrics)

  return (
    <>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-gray-500 border-b">
            <th className="py-1">Run started</th>
            {countKeys.map((key) => <th key={`c-${key}`}>{label(key)}</th>)}
            {metricKeys.map((key) => <th key={`m-${key}`}>{label(key)}</th>)}
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.run_id} className="border-b last:border-0">
              <td className="py-1">{item.run_created_at}</td>
              <Cells bucket={item} countKeys={countKeys} metricKeys={metricKeys} />
            </tr>
          ))}
          {unattributed && (
            <tr className="border-b last:border-0">
              <td className="py-1">Unattributed</td>
              <Cells bucket={unattributed} countKeys={countKeys} metricKeys={metricKeys} />
            </tr>
          )}
        </tbody>
      </table>
      <p className="text-xs text-gray-500 mt-2">Pending proposals are not counted as rejections.</p>
    </>
  )
}
