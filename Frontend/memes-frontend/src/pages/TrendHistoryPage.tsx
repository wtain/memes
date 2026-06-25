import { useEffect, useRef, useState } from "react"
import { useParams } from "react-router-dom"
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts"
import type { MemesApi } from "../api/MemesApi"
import type { TrendHistoryEntry } from "../types/generated/all"

type Props = { memesApi: MemesApi }

export default function TrendHistoryPage({ memesApi }: Props) {
  const { label, name } = useParams<{ label: string; name: string }>()
  const [entries, setEntries] = useState<TrendHistoryEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const loadedRef = useRef<string | null>(null)

  const decodedLabel = label ? decodeURIComponent(label) : ""
  const decodedName = name ? decodeURIComponent(name) : ""
  const key = `${decodedLabel}/${decodedName}`

  useEffect(() => {
    if (!decodedLabel || !decodedName) return
    if (loadedRef.current === key) return
    loadedRef.current = key
    void (async () => {
      await Promise.resolve()
      setLoading(true)
      setError(null)
      try {
        const data = await memesApi.getTrendsHistory(decodedLabel, decodedName)
        setEntries([...data].reverse())
        setLoading(false)
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err))
        setLoading(false)
      }
    })()
  }, [key, decodedLabel, decodedName, memesApi])

  return (
    <div className="space-y-6">
      <div>
        <p className="text-xs text-gray-400 uppercase tracking-wide">{decodedLabel}</p>
        <h1 className="text-2xl font-bold">{decodedName}</h1>
      </div>

      {loading && <p className="text-sm text-gray-400">Loading…</p>}
      {error && <p className="text-sm text-red-500">{error}</p>}

      {!loading && !error && entries.length === 0 && (
        <p className="text-sm text-gray-500">No history available.</p>
      )}

      {entries.length > 0 && (
        <>
          <div className="w-full h-64">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={entries} margin={{ top: 4, right: 16, left: 0, bottom: 4 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                <XAxis dataKey="date" tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
                <Tooltip />
                <Line type="monotone" dataKey="value" stroke="#2563eb" strokeWidth={2} dot={{ r: 3 }} />
              </LineChart>
            </ResponsiveContainer>
          </div>

          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="text-left bg-gray-100">
                <th className="p-2 border">Date</th>
                <th className="p-2 border text-right">Count</th>
              </tr>
            </thead>
            <tbody>
              {[...entries].reverse().map((e, i) => (
                <tr key={i} className="hover:bg-gray-50">
                  <td className="p-2 border">{e.date}</td>
                  <td className="p-2 border text-right tabular-nums">{e.value}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  )
}
